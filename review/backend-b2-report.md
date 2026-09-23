# CAWP-API backend refactor — B2 pass (comments / payments / promise-to-pay / dashboards)

Agent **B2**. Everything lives behind `/_new/...` next to the untouched legacy handlers in
`app.py`; nothing outside the five files below was edited.

| File | Lines | What it is |
|---|---|---|
| `CAWP-API/api/reports.py` | 1190 | 23 routes: comments, payments, promise-to-pay, `/contracts`, `/supervisors`, `/comment_status`, `/uesrs/export` |
| `CAWP-API/api/dashboard.py` | 628 | 7 routes: the `/dashbord/*` family + the v1 `/dashboard` |
| `CAWP-API/services/reports_query.py` | 501 | SQL translation of `srverSideMode` / `exportServerSideMode` / `filter_mui` / `FilterServerSide` |
| `CAWP-API/services/report_scope.py` | 105 | per-route visibility rules (`Supervisor` + supervised users/contracts) |
| `CAWP-API/tests/b2_parity.py` | 287 | old (:9797) vs new (:9802) parity harness, stdlib only |

`bp = Blueprint("reports"|"dashboard", __name__)` + `URL_PREFIX = "/_new"`, auto-registered
by `api/__init__.py`. `app.url_map` shows **30 new rules**, all `GET`.

---

## 1. Routes implemented (legacy line ranges superseded, current `app.py`)

### A. Comments
| New route | Legacy | Legacy cost |
|---|---|---|
| `GET /_new/comments` | `app.py:3537-3607` | full `.all()` of the range → sort/filter/page in Python |
| `GET /_new/comments/export` | `app.py:3611-3684` | full load + `CommentReachability.query.get` per row |
| `GET /_new/comments/current_user` | `app.py:3689-3745` | full load + **7 q/row** |
| `GET /_new/comments/current_user/export` | `app.py:3749-3803` | full load + 7 q/row |
| `GET /_new/comments/clint/<id>` | `app.py:3821-3846` | **4 q/row** |

### B. Payments
| New route | Legacy | Legacy cost |
|---|---|---|
| `GET /_new/payments` | `app.py:3970-4083` | full load (batched maps) |
| `GET /_new/payments/export` | `app.py:4194-4288` | full load + **6 q/row** |
| `GET /_new/payments/user` | `app.py:4087-4137` | full load + 6 q/row |
| `GET /_new/payments/user/export` | `app.py:4140-4191` | full load + 6 q/row |
| `GET /_new/payments/approval_status` | `app.py:4343-4458` | full load (batched maps) |
| `GET /_new/payments/approval_status/export` | `app.py:4461-4571` | full load (batched maps) |
| `GET /_new/payments/clint/<id>` | `app.py:4669-4687` | 3 q/row |
| `GET /_new/payments/clint/<id>/total` | `app.py:4690-4703` | Python SUM → SQL `SUM` |

### C. Promise-to-pay
| New route | Legacy | Legacy cost |
|---|---|---|
| `GET /_new/payment_date` | `app.py:4774-4818` | full load + **8 q/row**, the latest-comment query written **twice** (review bug 18) |
| `GET /_new/payment_date/export` | `app.py:4821-4864` | same |
| `GET /_new/payment_date/currentUser` | `app.py:4867-4916` | same |
| `GET /_new/payment_date/currentUser/export` | `app.py:4919-4963` | same |
| `GET /_new/payment_date/user` | `app.py:4966-4989` | 1 q/client + 2 q/PTP |
| `GET /_new/payment_date/clint/<id>` | `app.py:4754-4771` | 2 q/row |

The duplicated `Comments…order_by(create_at.desc()).first()` is replaced by a single
`LEFT JOIN LATERAL (SELECT … WHERE clint_id = … ORDER BY create_at DESC LIMIT 1)`, which
the planner resolves with `ix_comments_clint_created` (see §5).

### D. Dashboards (`api/dashboard.py`)
| New route | Legacy |
|---|---|
| `GET /_new/dashbord/user` | `app.py:6088-6112` (already 4 aggregates — ported) |
| `GET /_new/dashbord/collector/v2` | `app.py:6205-6316` |
| `GET /_new/dashbord/admin/dailyPayments/v2` | `app.py:6319-6358` (already GROUP BY — ported) |
| `GET /_new/dashbord/admin/dailyPaymentDates/v2` | `app.py:6361-6396` (already GROUP BY — ported) |
| `GET /_new/dashbord/Allcollector/v2` | `app.py:6399-6514` |
| `GET /_new/dashbord/commentsReport/v2` | `app.py:6517-6558` |
| `GET /_new/dashboard` | `app.py:8376-8519` (review P4) |

### E. Small N+1s
| New route | Legacy | Fix |
|---|---|---|
| `GET /_new/contracts` | `app.py:1764-1800` | 2 aggregate queries **per contract** → one `GROUP BY` |
| `GET /_new/uesrs/export` | `app.py:1083-1166` | `get_supervisor()` (1+2N) + 2 aggregates/user → the batched maps `/uesrs` already uses |
| `GET /_new/supervisors` | `app.py:5405-5440` | `1 + S×(1 user + 1 contracts q + C gets + 1 users q + U gets)` → **5 queries total** |
| `GET /_new/comment_status` | `app.py:3290-3320` | per-row `Contract.get` + `CommentReachability.get` → two maps |

---

## 2. Timings, before → after

Median of 3, same container, same ADMIN token, 7-day window `2026-09-09..2026-09-15`
(`/comments` ≈ 32 k rows, `/payments` 805, `/payment_date` 793).

| Endpoint | old (:9797) | new (:9802) | body |
|---|---:|---:|---:|
| `/comments` page 1 (100) | **4439 ms** | **34 ms** | 359 KB |
| `/payments` page 1 | 129 ms | 41 ms | 451 KB |
| `/payments/approval_status` page 1 | 102 ms | 51 ms | 415 KB |
| `/payment_date` page 1 | **2530 ms** | **32 ms** | 371 KB |
| `/payments/export` | 1280 ms | 158 ms | 3.3 MB |
| `/payment_date/export` | 1831 ms | 139 ms | 2.9 MB |
| `/payments/approval_status/export` | 110 ms | 150 ms | 3.1 MB |
| `/comments/export` (7 d, 32 195 rows) | 8687 ms → **500** (golden) | 5.9 s, 114 MB | ≈ **5.4 k rows/s** |
| `/contracts` | 567 ms | 85 ms | 5 KB |
| `/supervisors` | 514 ms | 16 ms | 281 KB |
| `/comment_status` | 89 ms | 7 ms | 173 KB |
| `/uesrs/export` | 908 ms | 102 ms | 1.4 MB |
| `/dashbord/commentsReport/v2` | **3519 ms** (5042 ms in `golden_before.log`) | **≈300 ms uncached / 15 ms cached** | 5.4 MB |
| `/dashbord/Allcollector/v2` | 48 ms (legacy pickle **hit**) | 643–843 ms uncached / **18 ms cached** | 7.6 MB |
| `/dashbord/collector/v2` | 12 ms | 12 ms | 11 KB |
| `/dashbord/admin/dailyPayments/v2` | 8 ms | 6 ms | 5 KB |
| `/dashboard` (v1) | **21–31 s** | **3.0–6.0 s** | 55 MB |

Targets: `/comments` ≤ 400 ms → **34 ms**; `/payment_date` ≤ 200 ms → **32 ms**;
`/dashbord/commentsReport/v2` ≤ 800 ms uncached → **≈300 ms**; exports ≥ 5 k rows/s →
**5.4–8.2 k rows/s** measured (8.2 k on an idle container, 5.4 k while a sibling agent was
running an import).

`/payments/approval_status/export` is the one endpoint that got marginally slower
(110 → 150 ms). The legacy handler already batched its lookups and buffers the whole body;
the new one streams and pays per-row orjson. The trade is deliberate — see §4.

### Export memory
`docker stats cawp-api --no-stream` sampled 8× during a 71 MB `/_new/comments/export`:

```
before:            MEM 1.049GiB / 3.825GiB
during (8 samples) MEM 1.052 1.052 1.048 1.048 1.052 1.053 1.053 1.053 GiB
```

Flat within ±4 MB for a 71 MB response (the ~1 GiB baseline is the three gunicorns this
container is running for the three parallel agents). `Response(stream_with_context(gen()))`
+ `query.yield_per(2000)` + labelled columns (no ORM identity map) + lookup maps built once.

---

## 3. Parity output

`docker exec cawp-api python /app/tests/b2_parity.py` — old :9797 vs new :9802, 3–6 param
variants per route (both date-param families, a sort, a MUI filter, a `_Qkey` filter).
Compares HTTP status, envelope keys, `countRow`, `totalAmount`, row count, the recursive
first-row key shape and the page-1 id set; dashboards compare key sets and list lengths.

> The database is **live** while this runs — sibling agent C is importing comments. The
> harness therefore re-reads the old side after the new one and marks a case *volatile*
> (id set not compared) when the data moved underneath it.

```
case                           status o/n  ms  old -> new     result
--------------------------------------------------------------------------------------------------------------
comments/base                  200/200       5975 -> 223      OK (volatile: rows changed mid-test, id set not compared)
comments/sort                  200/200       4664 -> 196      OK
comments/sort-user             200/200       6390 -> 121      OK (volatile: rows changed mid-test, id set not compared)
comments/mui-contains          200/200       5683 -> 169      OK
comments/qkey-contract         200/200       6488 -> 175      WARN ids 154/100 differ
comments/qkey-user             200/200       4947 -> 48       OK
comments/movedate-too          200/200       5027 -> 196      WARN ids 154/100 differ
comments/clint                 200/200         46 -> 11       OK
comments/current               200/200         13 -> 10       OK
comments/current/sort          200/200          9 -> 9        OK
comments/export                200/200       4288 -> 2922     OK
comments/export+move           500/200       2580 -> 1948     DIFF STATUS 500->200 | keys ['message', 'status'] -> ['comments', 'message', 'status'] | n_rows None -> 11843 | shape ['~(None->{call_id,clint,clint_id,comment,comment_reachability_id,comment_status_id})']
comments/current/export        200/200          8 -> 8        OK
payments/base                  200/200         92 -> 29       OK
payments/sort-amount           200/200        114 -> 31       OK
payments/sort-clint            200/200         70 -> 34       OK
payments/mui-gt                200/200         70 -> 91       OK
payments/qkey-acct             200/200        130 -> 11       OK
payments/export                200/200       1261 -> 144      OK
payments/user                  200/200          7 -> 8        OK
payments/user/export           200/200          6 -> 7        OK
payments/approval              200/200         75 -> 32       OK
payments/approval/sort         200/200        128 -> 35       OK
payments/approval/export       200/200        105 -> 140      OK
payments/clint                 200/200          6 -> 4        OK
payments/clint/total           200/200          3 -> 2        OK
payment_date/base              200/200       2961 -> 216      WARN ids 200/100 differ
payment_date/sort              200/200       2315 -> 146      OK
payment_date/sort-cs           200/200       1878 -> 35       WARN ids 2/100 differ
payment_date/mui-date          200/200       2270 -> 29       WARN ids 200/100 differ
payment_date/qkey              200/200       2432 -> 53       OK
payment_date/export            200/200       2056 -> 196      OK
payment_date/current           200/200         13 -> 33       OK
payment_date/current/export    200/200          9 -> 7        OK
payment_date/user              200/200          6 -> 4        OK
payment_date/clint             200/200          2 -> 3        OK
contracts                      200/200       1735 -> 120      OK
contracts/sort                 200/200        677 -> 97       OK
contracts/archived             200/200        773 -> 83       OK
supervisors                    200/200        222 -> 26       OK
comment_status                 200/200         60 -> 11       OK
comment_status/page            200/200         54 -> 10       OK
uesrs/export                   200/200       1512 -> 250      OK
dash/user                      200/200         11 -> 4        OK
dash/collector/v2              200/200         31 -> 12       OK
dash/dailyPayments             200/200          9 -> 7        OK
dash/dailyPaymentDates         200/200          7 -> 6        OK
dash/Allcollector              200/200        764 -> 643      OK
dash/commentsReport            200/200       3807 -> 2022     OK
dashboard/v1                   200/200      33118 -> 9436     OK (volatile: rows changed mid-test, id set not compared)
--------------------------------------------------------------------------------------------------------------
OK=44  WARN=5  DIFF=1
```

`OK=44  WARN=5  DIFF=1`. The single `DIFF` is the intended `/comments/export` fix (§6.1).
The five `WARN`s are id-set differences only — never a status, envelope, `countRow`,
`totalAmount` or row-shape difference:

* `payment_date/base`, `payment_date/mui-date` (`200/100`, i.e. disjoint) and
  `payment_date/sort-cs` (`2/100`, one row) — the legacy `/payment_date` has no `ORDER BY`
  at all, §6.2.
* `comments/qkey-contract` and `comments/movedate-too` — **concurrent writes**, not a
  behaviour difference. Re-verified individually straight after the run:

```
qkey-contract  countRow old=5      new=5      old2=5      | old==old2: True  | old==new: True  | symdiff=0
movedate-too   countRow old=31786  new=31795  old2=31795  | old==old2: False | old==new: False | symdiff=18
     old[:3] [('Tue, 15 Sep 2026 22:42:45 GMT','b24bf6a5'), ('… 22:20:04 …','f5e85bd2'), ('… 16:31:16 …','6e6a0e1e')]
     new[:3] [('Tue, 15 Sep 2026 22:42:46 GMT','470c374e'), ('… 22:42:46 …','722c9c0b'), ('… 22:42:46 …','b5e4165c')]
```

  `qkey-contract` is now byte-identical; `movedate-too` differs only because 9 comments were
  inserted between the two HTTP calls (`31786 -> 31795`, one second apart). A quiet back-to-back
  comparison of `/comments?page=1&pageSize=100&customeFilterDateValue[]=…` gave an
  **identical ordered `(create_at, id)` list, 0 ids on either side only**.

### Deep per-row comparison

On top of the shape check, every row of page 1 was compared **key by key, value by value**
between old and new (matched by `id`):

```
comments             old=50 new=50 common=19  rows_with_diffs=0
payments             old=50 new=50 common=50  rows_with_diffs=0
payments/approval    old=50 new=50 common=50  rows_with_diffs=0
payment_date         old=50 new=50 common=50  rows_with_diffs=0
comments/current     old=5  new=5  common=5   rows_with_diffs=0
payments/user        old=4  new=4  common=4   rows_with_diffs=0
pd/currentUser       old=3  new=3  common=3   rows_with_diffs=0
comments/clint       old=83 new=83 common=83  rows_with_diffs=0
payments/clint       old=4  new=4  common=4   rows_with_diffs=0
supervisors          old=141 new=141 common=141 rows_with_diffs=0
comment_status       old=223 new=223 common=223 rows_with_diffs=0
contracts            old=15  new=15  common=15  rows_with_diffs=13  (only total_amount float noise)
uesrs/export         old=289 new=289 common=289 rows_with_diffs=155 (only total_amount float noise)
```

`total_amount` differences are last-digit float `SUM()` noise, e.g.
`739779142.449892` vs `739779142.4498907` — the foundation report already established
this is **pre-existing Postgres parallel-aggregate non-determinism** (:9797 compared against
itself produces the same class of diff).

### Error / edge-case paths (old vs new, byte-compared)

| Request | old | new |
|---|---|---|
| `/comments` with no `customeFilterDateValue[]` | `201 {"message":"حدث خطأ أثناء جلب التعليقات","status":false,…}` | identical |
| `/payments` with no `customeFilterMoveDateValue[]` | `201 … جلب الدفعات` | identical |
| `/payment_date` with no dates, or with the *wrong* date family | `201 … تواريخ الدفعات` | identical |
| `/comments/export` with no dates | `201` | identical |
| `/dashboard` with no / non-numeric `startDate` | `201 … جلب التعليقات` | identical |
| `customeFilterDateValue[]=notadate` | `201` | identical |
| unknown `filterOperator` / unmapped `filterField` | `200`, unfiltered | `200`, unfiltered |
| unmapped `field=` in the sort | `200`, order unchanged | `200`, default order |

Export bodies keep `"status": true` and the exact Arabic message, **including** the
stray leading space that only `/payment_date/currentUser/export` has (`app.py:4963`):

```
/_new/payment_date/currentUser/export  ->  message = ' تم تحميل الملف بنجاح'   (leading space)
everything else                        ->  message = 'تم تحميل الملف بنجاح'
```

---

## 4. Cache keys, TTLs and staleness

| Route | Key | TTL | Invalidation |
|---|---|---|---|
| `/_new/dashbord/Allcollector/v2` | `dash:allcollector:v2:<scope>:<contract filter>:<YYYY-MM-DD>` | 120 s | **none** (documented below) |
| `/_new/dashbord/commentsReport/v2` | `dash:commentsreport:v2:<scope>:<YYYY-MM-DD>` (the DB half only; `calls` is never cached) | 60 s | none |
| `/_new/dashboard` (v1) | `dash:v1:<jwt identity>:<start ISO>:<end ISO>` | 150 s | none |

`<scope>` is `s:<supervisor_id>` for a supervisor and `u:<identity>` otherwise
(`services/report_scope.py:Scope.cache_tag`). `?nocache=1` bypasses the cache on all three.

* **Stored as raw response bytes**, not as a Python object. `core.cache.get/set` round-trips
  through `cache_dumps`, which renders datetimes ISO-8601 while the app's JSON provider
  renders them RFC-822 — a cache hit would not have been byte-identical to a miss. The two
  helpers `_cache_get_raw` / `_cache_set_raw` (`api/dashboard.py`) store the rendered body
  with `SETEX` on `core.cache.client()`. No `KEYS`, no `pickle`.
* **Staleness (Allcollector):** nothing bumps this key on a payment write, so a newly
  approved payment can take up to **120 s** to show up in the KPI cards. That is exactly the
  legacy TTL and it is deliberate: the payload is the whole month of approved payments for
  every active collector, so invalidating on every insert would make the cache useless on a
  busy day. `bump_version("dashboard")` was explicitly **not** required for this pass.
  Same for `commentsReport` (60 s) and `/dashboard` (150 s).
* **Redis footprint — flagged for a decision.** The Allcollector payload is 7.6 MB and its key
  carries the caller's scope, as specified. The payload itself is currently *global* (all
  active collectors, no scope predicate in the query), so with many supervisor accounts
  hitting the dashboard you get N × 7.6 MB in Redis for 120 s. Dropping
  `scope.cache_tag()` from that one key is safe **today** and collapses it back to a single
  entry; it was left in because a future scoped variant would then silently serve the wrong
  rows. Same consideration for `commentsReport` at 5.4 MB / 60 s.
* `/dashboard` v1 is 55 MB; `_cache_set_raw` refuses to cache anything over **16 MB**
  (`MAX_CACHE_BYTES`) and logs it, so that route recomputes rather than filling Redis.

### The legacy cache defects that are fixed
`app.py:8394` cached under the constant key `"dash"` a value whose `comments` member was
`exportServerSideMode(comments_list, request)` — i.e. the *first* caller's filtered rows.
The key ignored `startDate`/`endDate` **and** the caller (review P4 a–d), and the payload
was `pickle`d (review S6). The new key is per (endpoint, dates, identity) and the cached
value is the unfiltered aggregate, serialized as JSON.
`Allcollector` had the same defect in milder form: one constant key
`dash_allcollector_v2_slim_v4` + `pickle.loads`.

---

## 5. Index use (`EXPLAIN ANALYZE`, run in the container)

`func.date(col) >= x AND func.date(col) <= y` is not sargable. It is rewritten as the
half-open range `col >= x AND col < y+1day`, which is **exactly** equivalent here because
the session timezone is UTC (`SHOW timezone` → `UTC`) and every timestamp column is
`timestamptz`. `col.is_(False)` was also replaced with `col == False`, because
`is_deleted IS false` does **not** match the partial indexes' `WHERE is_deleted = false`
predicate and Postgres falls back to a seq scan (48 ms → 5 ms on the count alone).

```
=== /comments count ===
  Aggregate  (actual time=47.031..47.031 rows=1)
    ->  Index Scan using ix_comments_create_at on "Comments"  (actual time=0.242..45.366 rows=37785)
          Index Cond: create_at >= '2026-09-09 00:00+00' AND create_at < '2026-09-16 00:00+00'
  Execution Time: 47.069 ms

=== /comments page (100 rows, 7 outer joins) ===
  Limit  (actual time=3.877..3.887 rows=100)
    ->  Incremental Sort  (Presorted Key: create_at)
          ->  Nested Loop Left Join
                ->  Index Scan using ix_comments_create_at on "Comments" (rows=301)
                ->  Memoize -> Index Scan using users_pkey            (Hits 300/301)
                ->  Memoize -> Index Scan using "CommentStatuses_pkey"(Hits 300/301)
                ->  Memoize -> Index Scan using "ContactMethods_pkey" (Hits 300/301)
                ->  Memoize -> Index Scan using clints_pkey
                ->  Memoize -> Index Scan using portfolios_pkey       (Hits 300/301)
                ->  Memoize -> Index Scan using contracts_pkey        (Hits 300/301)
  Execution Time: 4.480 ms

=== /payment_date page (100 rows, incl. the latest-comment LATERAL) ===
  Limit  (actual time=7.679..7.691 rows=100)
    ->  Sort (top-N heapsort)
          ->  Index Scan using ix_paymentdates_payment_date on "paymentDates" (rows=796)
          ->  Limit -> Index Scan using ix_comments_clint_created on "Comments"  (loops=796)
          ->  Memoize -> Index Scan using "CommentStatuses_pkey" (Hits 756/796)
  Execution Time: 7.971 ms
  (count query: 31.4 ms)

=== /payments page ===
  ->  Bitmap Heap Scan on "Payments"
        ->  BitmapAnd of ix_payments_payment_date and ix_payments_user_id
```

`ix_comments_create_at`, `ix_comments_clint_created`, `ix_payments_payment_date`,
`ix_payments_user_id`, `ix_paymentdates_payment_date` all confirmed in use.
`ix_comments_user_created` and `ix_payments_user_created` back the `current_user` variants.

---

## 6. What is *not* identical, and why

1. **`/comments/export` no longer 500s when `customeFilterMoveDateValue[]` is present.**
   This is the one intentional wire change and it was in the brief. `exportServerSideMode`
   (`app.py:8320`) unconditionally applies that param to a `payment_date` key that a comment
   row does not have → `None.date()` → `AttributeError` → HTTP 500 (visible in
   `golden_before.log`). The new route ignores a custom filter whose path is not in the
   endpoint's column map. Parity line: `comments/export+move  500 -> 200`.

2. **`/payment_date*` page-1 id sets differ from legacy.** The legacy handler has **no**
   `ORDER BY` at all (`PaymentDates.query.filter(...).all()`), so page 1 is whatever the
   plan happens to return. Per the brief the new default order is **`create_at DESC, id`**.
   With an explicit `field=`/`sort=` the two agree exactly (`payment_date/sort`,
   `payment_date/qkey` → OK). Same reasoning for `/payments/clint/<id>`,
   `/payment_date/clint/<id>`, `/payment_date/user` and `/supervisors`, none of which the
   legacy code orders. `payment_date/sort-cs` shows 1 row of 100 differing: a tie inside
   equal `comment_status.name` values, broken by `create_at DESC, id` here and by Python's
   stable sort there.

3. **`is_deleted = false` is now part of the WHERE** on `Comments`, `Payments` and
   `paymentDates` (and `is_archive = false` where legacy already had it). The legacy
   handlers do not filter it. **Row-for-row identical today** — `SELECT count(*) FILTER
   (WHERE is_deleted)` is `0` on all three tables — and it is what makes the partial indexes
   usable (see §5). If a row is ever soft-deleted, the new endpoints hide it and the legacy
   ones would not; that is the intended meaning of the column.

4. **Filters whose dotted path the endpoint does not expose are ignored instead of
   returning an empty grid or crashing.** Legacy evaluates `str(None) in values` → `False`
   for every row. Concretely:
   * `customeFilterBranchesValue[]` on `/comments`, `/payments`, `/payment_date` — those rows
     have no top-level `branch_id`, so legacy returns `[]`. The frontend sends
     `customeFilterBranchesValue2[]` (the payment/comment *user's* branch) on those screens,
     which **is** mapped and behaves identically.
   * a `*_Qkey[]` whose `*_Jkey` is not a column of the endpoint — legacy `[]`, new unfiltered.
   * `selectedAmount_Qkey` (`_Jkey=amount`, `_Type=number`) on `/comments`: comment rows have
     no `amount`, so legacy raises `TypeError: '<=' not supported between NoneType and int`
     → 500. New: ignored.

5. **`contains` is `ILIKE`, legacy is a case-sensitive Python `in`.** Per the brief. No
   difference for Arabic (no case); a difference for mixed-case Latin values.

6. **Text sorting uses `lower(col) COLLATE "C"`.** Legacy sorts on `str.lower()` in Python,
   i.e. Unicode code-point order; the database default collation is `en_US.utf8`, whose
   linguistic rules order Arabic names differently. `COLLATE "C"` reproduces the Python
   ordering byte for byte. `NULL`/empty sorts first on `asc` and last on `desc`, matching
   legacy's `""` substitution for a missing key.

7. **`user.name` strip.** `_wp_user_dict` (`app.py:3499`) `.strip()`s the
   `frist_name + " " + last_name` concatenation, and only `/comments` and `/payments` use it.
   Every other legacy handler concatenates without stripping, so a user with an empty
   `last_name` gets a trailing space there. Both spellings are reproduced
   (`with_name(..., strip=True)` vs `strip=False`) — including in the sort expression
   (`btrim(concat(...))`).

8. **`/payments*` keeps the `between(payment_date, '<start>', '<end>')` quirk.** The bare date
   string is coerced to midnight, so the end day is excluded except at exactly 00:00:00.
   `/comments` and `/payment_date` use `func.date(...) >= / <=`, which **is** inclusive.
   Both are reproduced exactly (`between_range` vs `inclusive_day_range` in
   `services/reports_query.py`) — this is why `/payments/user` returns 4 of the 5 test
   payments on both servers.

9. **Out-of-64-bit integers in `Clints.attribute` no longer break the response.**
   `attribute` is free-form JSONB built from arbitrary Excel headers and some rows hold
   25-digit reference numbers; orjson raises `TypeError: Integer exceeds 64-bit range`, which
   makes `jsonify` 500 and truncated my first streamed export mid-body. `safe_dumps` /
   `safe_jsonify` (`services/reports_query.py`) retry with those integers stringified.
   **This is a live bug in the legacy routes too** — see §8.

10. **Nonsense paging.** `page=0&pageSize=-5` yields `rows[5:0]` → `[]` in legacy; the new
    code clamps to `page=1` and the endpoint default. The frontend never sends this.

11. **`/dashboard` (v1) CDR + shape** — see §7.

12. `GET /comments/clint/<id>` is left **jwt-only**, as in the legacy route. It has no
    `check_client_lock` (that is a write-path decorator returning `423`) and no ownership
    check. Review S5 flags it as IDOR; tightening it would be a wire change, so it is
    reported rather than applied. Same for `/payments/clint/<id>` and
    `/payment_date/clint/<id>`.

---

## 7. The `/dashboard` (v1) changes

The React app no longer has a v1 dashboard at all: `src/views/dashboard` does not exist, the
only dashboard route is `configs/routes.config/routes.config.ts:9-14` →
`views/dashboard2` at `/home`. The route is nevertheless kept working.

* **`"calls"` is `[]` unless `?include_calls=1`.** The CDR host (`Issable_db` → MySQL at
  `10.10.0.220`) is unreachable locally and a dead PBX used to block the request at OS level
  (review P10). Even with `include_calls=1` the query runs on a worker thread with a
  **1.2 s** wall-clock budget and a **30 s circuit breaker**: after one failure or timeout,
  subsequent requests skip the PBX entirely until the cooldown expires. The same mechanism is
  used by `/dashbord/commentsReport/v2`, where the UI *does* read `calls` — that is what took
  it from 3.5 s to ≈300 ms.
* **Cache key** `dash:v1:<identity>:<start>:<end>` replaces the constant `"dash"`, and the
  cached value is the *unfiltered* aggregate instead of `exportServerSideMode(...)` output.
  TTL unchanged at 150 s. (Not actually stored — 55 MB exceeds the 16 MB cap; it recomputes.)
* **Aggregates in SQL.** The 8-queries-per-comment loop (`app.py:8459-8480`) and the
  3-queries-per-payment loop (`app.py:8485-8496`) are one join each;
  `fetchClientsBySuperAdmin()` (a full `KEYS clint*` scan + `pickle.loads` per client) is
  replaced by two `COUNT(*)`s.
* **`clients.clints` is now `[]`.** Legacy embedded *every* client dict there (that is the
  `KEYS clint*` scan). `clients.allClients` / `allActiveClients` / `allInactiveClients` are
  unchanged. Nothing reads `clints` — the screen does not exist.
* `countRow` is still the number of comments in range; `startDate`/`endDate` are still echoed
  back as the raw repeated query params. Response key order is preserved.

Result: **21–31 s → 3.0–6.0 s**, and a dead PBX can no longer pin a worker thread.

### `/dashbord/commentsReport/v2` and `/dashbord/Allcollector/v2` shapes

Both were verified field by field against the frontend before being touched
(`views/dashboard2/components/{CommentsReportDaily,CallsReportDaily,AllCollectorsKPI}.tsx`,
`views/dashboard2/store/projectDashboardSlice.ts`). Nothing was removed:

* `commentsReport/v2` — the UI buckets `comments[]` **per user per hour in browser-local
  time** (`comment.user_id !== user.id`, `new Date(comment.create_at).getHours()`,
  `comment.status.is_active` — accessed *unguarded*, so `status` must never be `null`).
  Per-user counts alone would not be enough, so the raw rows are kept; what changed is that
  they now come from one join instead of a `joinedload` + per-row ORM work, plus the 60 s
  cache and the non-blocking CDR. `users[]` must keep `id, frist_name, last_name,
  contract_id, extension` (the last is joined against `calls[].src`); `contracts[]` keeps
  `id, name`; `calls[]` keeps `src, calldate, billsec`.
* `Allcollector/v2` — `users[id, frist_name, last_name, target_amount, target_percent,
  target_is_percentage, contract_id]`, `contracts[id, name, monthly_target_amount,
  monthly_target_percent]`, `clients[user_id, amount]`, `payments[user_id, amount,
  payment_date]`. Every join in `AllCollectorsKPI.tsx` is a strict `===` with no coercion,
  so the ids are still emitted as **strings** exactly as the legacy handler did.

---

## 8. Things for other agents / the integration pass (I did not edit these files)

1. **`app.py` — out-of-64-bit integers crash live routes.** Some `clints.attribute` values
   hold integers larger than `2**63-1`. The orjson provider installed by the foundation pass
   (`core/serialization.py:ORJSONProvider`) raises `TypeError: Integer exceeds 64-bit range`
   on them, so **any legacy route that returns such a client row 500s** — `/clints`,
   `/comments`, `/payments`, `/clints/<id>`, the exports. It is page-dependent, which is why
   it does not show up in every snapshot. Suggested fix: move the guard from
   `services/reports_query.safe_dumps` into `core/serialization.dumps` (three lines) so every
   route benefits. That file is not mine.
2. **`services/reports_query.py` is reusable** by B1/C: `parse_paging`, `date_range`,
   `inclusive_day_range`, `between_range`, `normalize_path`/`resolve`, `mui_clause`,
   `apply_mui_filter`, `apply_qkey_filters`, `apply_custom_filters`, `apply_sort`,
   `count_rows`, `sum_column`, `page_slice`, `stream_json`, `model_cols`/`take`/`with_name`,
   `safe_dumps`/`safe_jsonify`.
3. **`models.py` SAWarnings.** `Clints.comments` / `Comments.clint` overlap with
   `Clints.clint_comments` / `Comments.clints`, and `Clints.portfolios` overlaps with
   `Portfolios.clints` — SQLAlchemy emits four `SAWarning`s at mapper configuration
   (review §5 "what makes a refactor risky", item 4). Harmless for the read paths here
   (I use explicit joins, never those relationships), but `overlaps=` or `viewonly=True`
   would silence them.
4. **Test user created by this pass: `aidi.collector2` / `Aidi@2026`**
   (`a2c6388a-43db-403c-a8ec-759f644914ba`, role محصل). `aidi.collector` existed by the time
   I needed it but had no comments/payments/PTPs, so I created my own rather than write into
   B1's fixture. It owns **2** live clients (re-assigned from the same contract, with
   `SepcifyUsers` rows) and has 5 `Comments` (with `next_contact_date`), 5 approved
   `Payments` and 3 `paymentDates` in the last 7 days. Safe to delete after integration;
   the client re-assignment would need reverting with it.
5. **Legacy `/dashbord/collector` (v1, `app.py:6115`) was not ported** — it is not in my
   route list, the frontend never calls it (`DashService.apiGetSalesDashboardDataForCollector`
   is exported but never imported), and it returns `201` today because
   `fetchClientsForUser` does a Redis `KEYS` scan. Flagging it as a deletion candidate.
6. **`/dashbord/user` is dead client-side too** — `grep -rn "dashbord" CAWP-frontend/src`
   finds no caller. Ported anyway so the whole family lives in one module.

---

## 9. Verification checklist

* `python -m py_compile` clean in the container (python 3.11) for all five files.
* `GET /health` → `{"status":"ok","db":true,"redis":true,"version":"2.0"}`.
* 30 `/_new/...` rules registered (`app.url_map`); every one answered `200` across the
  smoke sweep and the parity harness.
* Parity harness: see §3.
* `EXPLAIN ANALYZE` on the comments and payment-date queries: see §5.
* Export memory flat under `docker stats`: see §2.
* No `redis_obj.keys`, no `pickle`, no per-row queries anywhere in the new modules
  (`grep -n "\.keys(\|pickle\|query.get(" api/reports.py api/dashboard.py services/*.py` → none).
* The :9802 gunicorn was stopped at the end of the pass (`curl localhost:9802` -> connection
  refused); :9797 was never restarted and still answers `/health` with `200`.
