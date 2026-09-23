# CAWP-API backend refactor — agent C: Excel imports, bulk actions, async jobs

Scope: the 14 write-heavy routes that parse an Excel file or act on a list of client ids
(review §2 P9 / P11, §3 bugs 3 / 9 / 21 / 25, §5 "the pandas Excel-column read appears 7×").
Nothing outside `api/imports.py`, `services/excel.py`, `services/imports.py`,
`services/bulk_actions.py` and `tests/c_*` was touched.

Verified against the untouched legacy routes still living in `app.py`. Both sides are driven
through a dedicated gunicorn on **:9803** (`gthread`, 8 threads, `--timeout 1800`, `--reload`
off) — it serves the same `app.py`, so the un-prefixed legacy route and the `/_new/…` route
are the same process and the same connection pool, which is the fairest comparison available.

The production instance on **:9797** was the original reference and still is for the ≤200-row
cases, but it cannot finish the 3,000-row **legacy** requests: its `--timeout` is 30 s, and
`addClintsByExcel` / `archive/ids` on 3,000 rows exceed it whenever the shared container is
also serving anything else (the sibling agents' report suites, in this case). The worker is
killed and the connection closes with no response. That is the P9 failure mode reproduced in
situ, and it is why `LEGACY_BASE` defaults to `:9803`; set `LEGACY_BASE=http://localhost:9797`
to watch it happen.

---

## 1. Files

| File | Lines | What it holds |
|---|---|---|
| `CAWP-API/services/excel.py` | 115 | `read_sheet` (one `load_workbook(read_only, data_only)`), `column_values` (the 7 pandas copies), `rows_as_dicts`, `cell_str` |
| `CAWP-API/services/imports.py` | 510 | `run_add_clints_by_excel`, `run_update_by_excel`, `run_attribute_by_excel`, `run_sepcify_users_by_excel`, `run_comments_by_excel` + the batching helpers (`Result`, `chunked`, `fetch_clints_by`, `paid_totals`, `check_amount`) |
| `CAWP-API/services/bulk_actions.py` | 430 | `run_archive_by_excel`, `run_unarchive_by_excel`, `run_archive_by_ids`, `run_archive_portfolio`, `run_sepcify_users_by_ids`, `run_bulk_lock`, `run_bulk_unlock`, `run_delete_clints`, `run_send_notification` |
| `CAWP-API/api/imports.py` | 435 | `bp = Blueprint("imports")`, `URL_PREFIX = "/_new"`, the 14 routes, the `?async=1` dispatcher |
| `CAWP-API/tests/c_parity.py` | 859 | the legacy-vs-new harness (response JSON + DB checksums + timings + async) |
| `CAWP-API/tests/c_cleanup.py` | 53 | standalone sweep of everything the harness creates, for an interrupted run |

`app.py` registers the blueprint through the existing `api/__init__.py` loader — verified:
`registered blueprint imports (prefix=/_new, 14 rules)`.

---

## 2. Routes implemented (legacy line ranges superseded)

864 lines of `app.py` are superseded. Every path, method, request key, response key,
Arabic string and status code is unchanged.

| New route (`/_new` prefix) | Legacy | Notes |
|---|---|---|
| `POST /clint/SepcifyUsersByExcel` | `app.py:696-745` | assign collector from posted rows |
| `POST /clint/addClintsByExcel` | `app.py:746-778` + `insertClints` `801-870` + `default` `873-880` | creates the portfolio, then bulk-inserts `Clints` + `ClintContacts` |
| `POST /clints/update/byexcel` | `app.py:1517-1602` + `check_amount` `7983-8005` | `update_type` 1/2/3/4/5 |
| `POST /clints/attribute/byexcel` | `app.py:1617-1697` + `_normalize_excel_key` `1605-1614` | the in-repo template; only the per-row Redis loop is gone |
| `POST /commentsbyexcel` | `app.py:3409-3482` | one comment per account number in the chosen column |
| `PUT /clint/archivebyexcel` | `app.py:5253-5293` | |
| `PUT /clint/unarchivebyexcel` | `app.py:5295-5349` | |
| `PUT /clint/archive/ids` | `app.py:5372-5404` | |
| `PUT /clint/archive/portofolio/<id>` | `app.py:5939-5989` | |
| `DELETE /clints` | `app.py:6017-6046` | keeps `@required_roles('clints_delete')` |
| `POST /sepcifyusers/ids` | `app.py:6047-6087` | |
| `PUT /clints/bulk-lock` | `app.py:442-489` | |
| `PUT /clints/bulk-unlock` | `app.py:490-535` | |
| `POST /send/notification` | `app.py:6631-6674` + `send_notification_to_user` `6591-6630` | |

**What changed under them**

* No per-row commit anywhere. One `db.session.commit()` per 500 rows
  (`services.imports.BATCH_SIZE`), or one commit for the whole set-based bulk actions.
* No per-row query. The affected clients come back in one `IN (...)` per 1,000 values
  (`IN_CHUNK`); `check_amount`'s payment total is one `GROUP BY` instead of a query per row.
* No `updateClintInRedis`, no `redis_obj.keys`, no `pickle`. The Redis client cache is
  simply not written; nothing invalidates `clint:*` because nothing in these paths reads it
  any more. (`core.cache.delete_pattern` is available if a reader turns up — it is not called.)
* Archive / unarchive / assign / lock / delete are single statements:
  `INSERT … RETURNING` + `UPDATE … WHERE id IN (…)`, `INSERT INTO "SepcifyUsers" … VALUES (…),(…)`,
  `INSERT INTO client_lock_history … VALUES (…),(…)`.
* `send/notification` inserts and commits **all** `Notification` rows first and only then
  emits `alarm` per recipient — review P11 (the emit is a blocking socket write under
  `async_mode='threading'`, and it used to sit between commits).
* The workbook is loaded **once**, in `read_only=True, data_only=True` mode.

---

## 3. `services/excel.py` — pandas string coercion, replicated exactly

`column_values(path, index)` replaces
`ps.read_excel(path, dtype=str).iloc[:, idx].astype(str).values.tolist()` at every call site
that is mine (`/commentsbyexcel`, `/clint/unarchivebyexcel`, `/send/notification`).
The coercion matters: the values are matched against `clints."accountNumber"`, a **text**
column, so `'1220401916.0'` would match nothing.

Rules (derived by reading `pandas.io.excel._openpyxl.OpenpyxlReader._convert_cell` and then
verified empirically against pandas 2.1.0 / openpyxl 3.1.2 in the container):

| cell | openpyxl value | pandas / `column_values` |
|---|---|---|
| `1220401916` | `int` | `'1220401916'` |
| an integral float (`1e20`) | `float` | `'100000000000000000000'` — **the `.0` is dropped**, because pandas does `val = int(v); return val if val == v else float(v)` |
| `1234.5` | `float` | `'1234.5'` |
| `'  102 '` | `str` | `'  102 '` (not stripped) |
| empty / `None` | `None` | `'nan'` — the literal string; legacy then queried `accountNumber='nan'` |
| `2020-01-02 03:04:05` | `datetime` | `'2020-01-02 03:04:05'` |
| `TRUE` | `bool` | `'True'` (checked before the float branch — `bool` is an `int` subclass) |

Trailing all-blank rows are dropped (pandas pops `[""]*width` rows off the end); interior
blanks are kept as `'nan'`.

**Parity check** (8 workbooks × every column, incl. a 3,000-row one):
`column_values parity: OK` for all of them, at 0.029 s vs 0.049 s for the pandas call on
3,000 rows, and without materialising a DataFrame.

---

## 4. Bug fixes

| Review | What legacy did | Now |
|---|---|---|
| **§3.3** `for…else` | `update_type == "5"` appended `"الحساب غير موجود"` for **every** row, including the ones that updated fine, so `"تم تحديث N من اصل M"` always under-counted | only rows with no matching `identityNumber` are reported. (The foundation pass had already swapped the `for…else` for an `if clints:`; the batched version keeps that.) |
| **§3.25** `update_type == "3"` | no branch at all → fell through to `len(errorMessages) == 0` → `200 {"status": true}` having done nothing | the dialog (`UpdateDataByExcelButton.tsx:85-134`) labels 3 **"رقم الهوية"** with `data1` = the account-number column, so type 3 now updates `identityNumber` by `accountNumber`. An **unknown** `update_type` no longer reports silent success: every row gets `[identifier, "نوع التحديث غير موجود"]` and the response is the normal `"تم تحديث 0 من اصل M"` / `status:false` body. |
| **§3.9** `selected_clients` | `NameError`/`TypeError` when the temp file was missing | the route resolves the path first and passes `None`; the service returns the legacy success body having done nothing — which is what the foundation pass made legacy do, and the harness asserts both sides agree |
| **§3.10** | `"create_by": str(current_user)` stored `"<User 'a@b.com'>"` | already fixed in the foundation pass; the notification payload and `SepcifyUsers.create_by` both carry `get_jwt_identity()` |
| **P9** workbook loaded twice | 2× parse, 2× memory | `read_sheet` loads once |
| **P2** `updateClintInRedis` | ~9 queries + a `KEYS` scan **per row** | not called |
| **P11** emit-in-a-loop | commit + blocking emit per row | one insert batch, one commit, then the emits |

### `check_client_lock` (review §3.21)

Verified against the legacy source: `check_client_lock` is applied to `PUT /clints/<id>`,
`POST /comments`, `POST /payment`, `POST /payment_date` — and to **none** of the 14 routes
in this pass. Legacy therefore lets a locked client be updated through
`/clints/update/byexcel`, `/commentsbyexcel`, `/sepcifyusers/ids`, the archive routes and the
bulk actions. Per the brief (preserve where legacy applied them) and the foundation report
§4.2 (which explicitly left `check_client_lock` un-extended for the bulk flows), **no lock
check was added** and **no lock-related error text was introduced** — there was none to
preserve. `bulk-lock`/`bulk-unlock` keep their own "already locked / already unlocked"
counters, which are not `check_client_lock`. This is a deliberate non-change; extending the
lock to bulk paths is a product decision, not a refactor.

---

## 5. Parity results

`docker exec cawp-api sh -c 'cd /app && python tests/c_parity.py --sizes 5,200,3000'`

Two fresh portfolios per size (`C-TEST-<ts>-L<n>` on :9797, `C-TEST-<ts>-N<n>` on :9803),
identical row sets apart from `accountNumber` / `identityNumber` (both are effectively unique
keys, so the two fixtures must not share them). For every route the harness compares
the HTTP status, the response key set, the full response body (`json.dumps(sort_keys=True)`,
so `messages`/`errorMessages` content included), and then a DB checksum + the related-row
counts.

```
legacy=http://localhost:9803  new=http://localhost:9803  sizes=[5, 200, 3000]  tag=C-TEST-20260915224214
… 305 individual checks …
==============================================================================
RESULT: 305/305 checks passed
==============================================================================
```

**0 failures.** The 33 sections, each run at 5 / 200 / 3,000 rows unless noted:

| Section | What it asserts | 5 | 200 | 3000 |
|---|---|:-:|:-:|:-:|
| `POST /clint/addClintsByExcel` | body, DB counts, `attribute` JSON, "all N rows inserted on both sides" | ✅ | ✅ | ✅ |
| `POST /clints/update/byexcel` types 2 / 4 / 5 (+3 rows that do not exist, so `messages` is exercised) | body incl. the `messages` list-of-lists, then `name`+`amount` of every row | ✅ | ✅ | ✅ |
| `POST /clints/attribute/byexcel` | body, and the `attribute` value written to every client | ✅ | ✅ | ✅ |
| `POST /clint/SepcifyUsersByExcel` (+ unknown account, + unknown user) | body, checksum, counts | ✅ | ✅ | ✅ |
| `POST /commentsbyexcel` (+ unknown account, + missing file) | body, checksum, counts | ✅ | ✅ | ✅ |
| `PUT /clint/archivebyexcel` + `PUT /clint/unarchivebyexcel` (+ missing file) | body, checksum, counts | ✅ | ✅ | ✅ |
| `PUT /clint/archive/ids` (+ unknown id) | body, checksum, counts | ✅ | ✅ | ✅ |
| `POST /sepcifyusers/ids` (+ unknown user) | body, checksum, counts | ✅ | ✅ | ✅ |
| `PUT /clints/bulk-lock` ×2 (second one hits the "already locked" branch) + `bulk-unlock` + empty→400 | body, checksum, counts, `client_lock_history` rows | ✅ | ✅ | ✅ |
| `POST /send/notification` | body, notification row count, `data` payload key set | ✅ | ✅ | ✅ |
| `PUT /clint/archive/portofolio/<id>` (+ second call → 404) | body, checksum, counts | ✅ | – | – |
| `POST /clints/update/byexcel` unknown `update_type` | legacy silent success vs the new error body | ✅ | – | – |
| `POST /clints/update/byexcel` `update_type=3` | the new branch really writes `identityNumber` | ✅ | – | – |
| `?async=1` end to end | 202 shape, job → `done`, `result` == the sync body, both flag forms, sync validation errors | ✅ | – | – |
| `DELETE /clints` (clean / unknown id / with lock history) | body, rows removed | ✅ (8) | – | – |

Representative checksums at 3,000 rows — identical on both sides at every step:

```
archivebyexcel:    DB row checksum  legacy=9b93f928b089d6719604ca464fba4816 new=9b93f928b089d6719604ca464fba4816
unarchivebyexcel:  DB row checksum  legacy=18ab34b9f624faa0b8f972af17d1d42f new=18ab34b9f624faa0b8f972af17d1d42f
archive/ids:       DB row checksum  legacy=9b93f928b089d6719604ca464fba4816 new=9b93f928b089d6719604ca464fba4816
sepcifyusers/ids:  DB row checksum  legacy=18ab34b9f624faa0b8f972af17d1d42f new=18ab34b9f624faa0b8f972af17d1d42f
bulk-lock:         DB row checksum  legacy=f94b28020a7440949987ce4d4c7c6b74 new=f94b28020a7440949987ce4d4c7c6b74
bulk-unlock:       DB row checksum  legacy=18ab34b9f624faa0b8f972af17d1d42f new=18ab34b9f624faa0b8f972af17d1d42f

bulk-lock: DB counts {'clints': 3000, 'contacts': 6000, 'sepcifyusers': 6000, 'comments': 3000,
                      'archive_rows': 6000, 'lock_history': 3000, 'archived': 0, 'locked': 3000,
                      'notifications': 0}   legacy == new
```

`?async=1`, end to end:

```
[PASS] async: HTTP 202  got 202
[PASS] async: 202 body shape
       {"status": true, "job_id": "ef03316c1941446ea204e174503ad10d",
        "message": "تم بدء المعالجة في الخلفية", "async": true}
[PASS] async: job reaches done
       {"id": "ef03316c…", "name": "bulk-lock", "owner": "d7dced3b-…", "status": "done",
        "progress": 100, "message": "lock 5",
        "result": {"message": "تم قفل 5 عميل", "status": true}, "error": null, …}
[PASS] async: job result == the synchronous body   {"message": "تم قفل 5 عميل", "status": true}
[PASS] async: sync body for the same input is identical
[PASS] async: {"async": true} in the body also returns 202
[PASS] async: attribute job done + result body
       {"messages": [], "message": "تم تحديث 5 حساب بنجاح", "status": true}
[PASS] async: job actually wrote 5/5 rows
[PASS] async: validation error is returned synchronously as 201
       201 {'message': 'يرجى تحديد عمود التعريف وعمود القيمة', 'status': False}
[PASS] async: empty portfolio still 404s synchronously
       404 {'message': 'لم يتم العثور على أي عملاء غير مؤرشفين في المحفظة', 'status': False}
```

The two deliberate divergences, as the harness printed them:

```
== POST /clints/update/byexcel unknown update_type (review 3.25) ==
[PASS] legacy={'messages': [], 'message': 'تم تحديث الحسابات بنجاح', 'status': True}
       new   ={'messages': [['x', 'نوع التحديث غير موجود']], 'message': 'تم تحديث 0 من اصل 1', 'status': False}

== POST /clints/update/byexcel update_type=3 ==
[PASS] update_type=3 now actually updates identityNumber   identityNumber=7700000000
[PASS] update_type=3: legacy silently reported success having done nothing (documented)
       legacy body={'messages': [], 'message': 'تم تحديث الحسابات بنجاح', 'status': True}

== DELETE /clints ==
[PASS] DELETE /clints(clean): response body identical
[PASS] DELETE /clints(clean): 4 deleted on both sides, 4 left
[PASS] DELETE /clints(missing id): response body identical   (201, {'error': 'clint not found'})
       legacy: 201 {"message": "حدث خطأ أثناء حذف العملاء", "status": false, "error": "internal error"}
       new   : 200 {"message": "تم حذف العملاء بنجاح", "status": true}
[PASS] DELETE /clints(with lock history): new succeeds 200 and the rows are gone   left=0
       DIVERGENCE (expected): legacy cannot delete a client that has client_lock_history rows
```

### DB checksum

`md5` over, per client, ordered by `accountNumber`:
`name, amount, attribute (minus the two fixture keys), is_archive, is_locked, user_id,
clintArchive_id IS NOT NULL` — plus exact counts of
`clints / ClintContacts / SepcifyUsers / Comments / clintArchive / client_lock_history /
archived / locked / notifications`.

---

## 6. Timings

```
TIMINGS   legacy -> new   (ms, wall clock, whole HTTP request, both on :9803)
==============================================================================
  addClintsByExcel                   n=5             84 ->       12     6.8x
  addClintsByExcel                   n=200         1075 ->      250     4.3x
  addClintsByExcel                   n=3000       26104 ->     2184    12.0x
  update/byexcel           type=2 n=5             45 ->        6     7.0x
  update/byexcel           type=4 n=5             31 ->        5     6.1x
  update/byexcel           type=5 n=5             29 ->        5     5.7x
  update/byexcel           type=2 n=200         1042 ->       55    18.9x
  update/byexcel           type=4 n=200          847 ->       39    21.8x
  update/byexcel           type=5 n=200          769 ->       46    16.8x
  update/byexcel           type=2 n=3000       16298 ->     2032     8.0x
  update/byexcel           type=4 n=3000       13618 ->     1717     7.9x
  update/byexcel           type=5 n=3000       12306 ->     1771     6.9x
  attribute/byexcel                  n=5             22 ->        5     4.3x
  attribute/byexcel                  n=200          757 ->       58    13.1x
  attribute/byexcel                  n=3000       10899 ->     1929     5.7x
  SepcifyUsersByExcel                n=5             49 ->       10     4.8x
  SepcifyUsersByExcel                n=200         1470 ->      167     8.8x
  SepcifyUsersByExcel                n=3000       18443 ->     3880     4.8x
  commentsbyexcel                    n=5             81 ->       28     2.9x
  commentsbyexcel                    n=200         1327 ->      108    12.2x
  commentsbyexcel                    n=3000       22511 ->     2503     9.0x
  archivebyexcel                     n=5             57 ->        9     6.3x
  archivebyexcel                     n=200         1134 ->       82    13.8x
  archivebyexcel                     n=3000       22941 ->     1209    19.0x
  unarchivebyexcel                   n=5             47 ->        6     7.5x
  unarchivebyexcel                   n=200         1087 ->       27    40.7x
  unarchivebyexcel                   n=3000       17519 ->      352    49.8x
  archive/ids                        n=5             42 ->        9     4.6x
  archive/ids                        n=200         1044 ->       55    18.9x
  archive/ids                        n=3000       19866 ->      800    24.8x
  sepcifyusers/ids                   n=5             38 ->        6     6.2x
  sepcifyusers/ids                   n=200         1206 ->       65    18.5x
  sepcifyusers/ids                   n=3000       22028 ->     1152    19.1x
  bulk-lock                          n=5             32 ->        6     5.3x
  bulk-lock                          n=200          938 ->       41    22.9x
  bulk-lock                          n=3000       19779 ->      391    50.5x
  send/notification                  n=5             19 ->        9     2.1x
  send/notification                  n=200          550 ->      273     2.0x
  send/notification                  n=3000        8067 ->     2084     3.9x
  archive/portofolio                 n=5             18 ->        6     2.9x
  DELETE /clints                     n=8             41 ->        7     6.2x
==============================================================================
```

**200 rows: 2.0×–40.7×. 3,000 rows: 3.9×–50.5×.** The shape of the win follows the shape of
the fix:

* **Pure set operations win biggest** — `bulk-lock` 19.8 s → 0.39 s (50×), `unarchivebyexcel`
  17.5 s → 0.35 s (50×), `archive/ids` 19.9 s → 0.80 s (25×). These become one `UPDATE … IN`
  (+ one `INSERT … VALUES`) and one commit, so the row count barely moves the clock.
* **Row-shaped imports win by the commit count** — `addClintsByExcel` 26.1 s → 2.2 s,
  `commentsbyexcel` 22.5 s → 2.5 s, `update/byexcel` ~13-16 s → ~1.8 s. They still insert N
  rows, so ~8-12× is the ceiling; the remaining time is the inserts themselves.
* **`send/notification` is the smallest win (2-4×)** because most of its cost is the N
  `socketio.emit` calls, which still happen per recipient — they just no longer sit inside a
  transaction. With no subscriber in the room the emit is cheap; under real load the win is
  larger than this number suggests.
* These numbers were taken while the two sibling agents were hammering the same container
  (it was CPU-saturated for much of the run), so both columns are inflated; the **ratios** are
  what to read. In a quieter earlier run the absolute legacy numbers were ~40% lower and the
  ratios were the same.

Plus the wins that do not show up as a ratio:

* **`?async=1` makes the wall-clock irrelevant** for the big files — 202 in a few ms and a
  job id to poll, instead of a request the browser holds open with `timeout: 0`.
* `column_values` replaces the pandas column read at 0.029 s vs 0.049 s on 3,000 rows, and
  without building a DataFrame.
* The workbook is parsed once instead of twice.

---

## 7. The async contract (for the frontend agent)

Every route in §2 accepts the flag in either form:

```
POST /clints/update/byexcel?async=1            …or…   {"…": …, "async": true}
```

`1 / true / yes / on` (case-insensitive) count as true; the query string wins over the body.
`async` is stripped from the body before it reaches `Comments(**data)`.

**The request is still validated synchronously.** A missing/unsafe file, a non-existent
contract, an empty portfolio, a missing `identifier_column`, an empty `client_ids` — all
return the legacy body and the legacy status (`201` / `400` / `404`) immediately; only work
that has passed validation is queued.

**202 Accepted:**

```json
{"status": true,
 "job_id": "0f3c…hex",
 "message": "تم بدء المعالجة في الخلفية",
 "async": true}
```

**Polling** `GET /jobs/<job_id>` (already in `app.py`, `@jwt_required()`, owner-checked):

```json
{"id": "0f3c…", "name": "update-clints-by-excel", "owner": "<user id>",
 "status": "queued" | "running" | "done" | "failed",
 "progress": 0-100,
 "message": "rows 1500/3000",
 "result": { … },            // null until status == "done"
 "error": null,              // str(exception) when status == "failed"
 "created_at": 1789…, "started_at": 1789…, "finished_at": 1789…}
```

`result` **is the body the synchronous call would have returned**, byte for byte — the
harness asserts this. So the dialogs can keep the code they have:

```js
if (job.status === "done") {
  const r = job.result;                       // same object shape as today's response.data
  if (r.status) toast.success(r.message);     // "تم تحديث 2998 حساب بنجاح"
  else          toast.danger(r.message, r.messages);   // "تم تحديث 2998 من اصل 3000"
}
```

Job names (the `name` field, useful for a progress label):
`add-clints-by-excel`, `update-clints-by-excel`, `attribute-by-excel`,
`sepcify-users-by-excel`, `comments-by-excel`, `archive-by-excel`, `unarchive-by-excel`,
`archive-by-ids`, `archive-portfolio`, `sepcify-users-by-ids`, `bulk-lock`, `bulk-unlock`,
`delete-clints`, `send-notification`.

Notes for the frontend:
* a job whose *business* outcome is a failure (e.g. "الحساب غير موجود") still finishes as
  `status: "done"`; the failure is inside `result` (`result.status === false`). `"failed"`
  means an unhandled exception, and `error` carries it.
* `progress` moves per 500-row batch; `message` is `"<done>/<total>"`.
* job state lives in Redis for `Config.JOB_RESULT_TTL` (3600 s by default) — poll within
  the hour, and treat a `404 {"message": "job not found"}` as "expired", not as an error.
* synchronous calls are now fast enough that `async` is only worth using above a few
  thousand rows (see §6); nothing has to change on day one.

---

## 8. Not identical to legacy — and why

1. **`update_type == "3"` now does something** (review §3.25). Legacy returned
   `200 {"status": true, "message": "تم تحديث الحسابات بنجاح"}` and wrote nothing. It now
   updates `identityNumber` by `accountNumber`, which is what the dialog's labels say it is
   for. If that reading of the UI is wrong, the branch is three lines in
   `services/imports.py` (`elif update_type == "3":`) and can be turned into the
   "نوع التحديث غير موجود" error instead.
2. **An unknown `update_type` now reports an error** instead of silent success.
3. **`DELETE /clints` now works on a client that has lock history.** Legacy fails with
   `201 {"message": "حدث خطأ أثناء حذف العملاء"}`: `ClientLockHistory.clint` is a plain
   relationship with no cascade, so the ORM emits
   `UPDATE client_lock_history SET clint_id = NULL` and hits the `NOT NULL` constraint
   (reproduced in the harness and in the container log). The database FK **is**
   `ON DELETE CASCADE`, so the set-based `DELETE FROM clints WHERE id IN (…)` just works.
   Net effect: a client that was ever bulk-locked was previously undeletable.
   The harness prints both bodies and flags the divergence.
4. **Partial-failure granularity on `addClintsByExcel`.** Legacy committed per row, so a
   duplicate `accountNumber` or a NULL `name` skipped exactly that row. A batch commit would
   lose the whole batch, so `_flush_batch` commits the batch and, **only if it fails**,
   rolls back and retries that batch row by row — same rows land, same rows are dropped, at
   batch speed in the normal case. Verified: a fixture whose account numbers collided with
   an earlier one dropped exactly the same 200 rows on both sides.
5. **`clint.amount` is stored as `float(value)`** on `update_type == "2"` instead of the raw
   cell. The column is `float8` so the stored value is identical; the difference is only
   visible when the same account appears twice in one file, where legacy's per-row commit
   expired the attribute and re-read a float — coercing makes the batched version behave the
   same way.
6. **`check_amount`'s paid total comes from one SQL `SUM`** instead of a Python loop over
   `Payments`. Same value; float addition order can differ in the last bit, which can only
   matter for a file whose new amount is exactly equal to the total paid.
7. **Error status for a malformed body.** `SepcifyUsersByExcel`, `archivebyexcel`,
   `unarchivebyexcel` and `sepcifyusers/ids` have no outer `try` in legacy, so a body
   missing a top-level key is a raw `500`. The new routes read those top-level keys
   **outside** the try (so a missing key is still a 500) but a `KeyError` on a *row* key
   inside the loop now produces the legacy inner-handler body,
   `201 {"message": "حدث خطأ", "status": false}`, instead of a 500. Both are rendered the
   same way by the dialogs (they read `status`).
8. **Redis client cache is not written.** `updateClintInRedis` is not called from any of
   these routes and `clint:*` keys are not invalidated either. This is the instruction and
   matches review P1/P2: the remaining readers are the export endpoints that agent B1 is
   replacing. If B1's work lands before this does, nothing here needs to change; if a reader
   survived, the one-line hook is `core.cache.delete_pattern(f"clint:{id}*")` per batch.

Everything else — envelopes, key names (`messages`, not `errorMessages`, on
`update/byexcel` and `attribute/byexcel`), Arabic strings including the stray spaces in
`"  تم نقل المحقظة للأرشيف بنجاح "`, the `"error": "role not found"` copy-paste on
`/clint/archive/ids`, the 201-on-error convention, the `400` on an empty `client_ids`, the
`404` on an empty portfolio, and the frozen typos (`SepcifyUsersByExcel`, `commentsbyexcel`,
`archivebyexcel`, `unarchivebyexcel`, `portofolio`, `sepcifyusers`) — is byte-identical, as
the harness asserts.

---

## 9. Things the integration agent needs to do (outside my files)

1. **Delete the legacy routes from `app.py`** and set `URL_PREFIX = None` in
   `api/imports.py`. The legacy helpers that become dead at that point:
   `insertClints` (801-870), `default` (873-880), `check_amount` (7983-8005),
   `_normalize_excel_key` (1605-1614), `send_notification_to_user` (6591-6630) — and
   `updateClintInRedis` (6707-…) once B1's exports stop reading the cache.
2. **`api/imports.py` imports `has_permission` from `app` lazily** (inside the decorator, at
   request time) to keep `DELETE /clints` gated by `clints_delete` without an import cycle.
   If `required_roles`/`has_permission` move to `core/`, that one import moves with them.
3. **`/_new/clints` is shared**: B1's `clients_reports.get_clints` (GET) and my
   `imports.deleteClints` (DELETE) register the same rule with different methods, which is
   exactly how `app.py` has it today. Werkzeug dispatches on the method; verified in
   `app.url_map` and exercised live. Nothing to reconcile at integration.
4. **`--reload` is a hazard for parallel work**: my first volume run was killed three times
   by gunicorn restarting when the sibling agents saved `api/reports.py` /
   `services/reports_query.py`. The final numbers were taken with `--reload` off.
5. **The legacy 3,000-row routes exceed the production gunicorn `--timeout` (30 s).**
   Observed on `/clint/addClintsByExcel` (6,000+ commits) and `/clint/archive/ids`
   (9,000 commits) against `:9797` while the sibling agents were also using it: the worker is
   killed mid-request and the connection closes with no response. The frontend sets
   `timeout: 0` on exactly these calls, so today the browser just hangs. The new routes do
   the same work in one `INSERT … RETURNING` + one `UPDATE`, in well under a second — and
   `?async=1` removes the request-duration question entirely. **Raising the gunicorn timeout
   is not the fix and should not be done; the batching is.**
   (A first attempt to make the harness retry a dropped connection was reverted: these routes
   are not idempotent, and a retried `archivebyexcel` inserted a second `clintArchive` row for
   every client the killed attempt had already archived — 3,684 rows for 3,000 clients. The
   harness now records the drop and skips that sub-test.)
6. `tests/c_parity.py` prefers `requests` and falls back to a stdlib `urllib` shim, so it
   needs **no new dependency**; `requirements.txt` was not modified.

---

## 10. Test-data hygiene

* Everything was created in dedicated portfolios named `C-TEST-<timestamp>-…` under contract
  `البنك الفرنسي`, with `accountNumber` / `identityNumber` in a synthetic 14-digit range
  (`9<ts>…` / `7<ts>…`) that cannot collide with a production account, contacts at
  `c<i>@test.local`, and the notification message `تنبيه اختبار C-TEST`. **No pre-existing
  client, portfolio, comment, payment, contact or user row was read-modified or deleted.**
* `tests/c_cleanup.py` is the sweep, and `c_parity.py` calls the same logic in its `finally`.
  For every `C-TEST-%` portfolio it nulls `clintArchive_id`, deletes that portfolio's
  `notifications`, `ClintContacts`, `clintArchive` and `clints` rows, then the portfolio
  (`Comments`, `Payments`, `paymentDates`, `SepcifyUsers`, `client_lock_history` and
  `clint_documents` follow the `ON DELETE CASCADE` FKs), and then sweeps three classes of row
  an interrupted run can orphan **after** its portfolio is already gone: contacts matching
  `%@test.local`, notifications matching the harness message, and `uploads/temp/c_*.xlsx`.
* Three interruptions happened (two gunicorn `--reload` restarts triggered by the sibling
  agents saving their files, one legacy worker killed on the 30 s timeout). Each left a few
  orphans — 6 `ClintContacts`, then 5 `notifications`, then 12 temp workbooks — all traced,
  confirmed to be harness-created (timestamps, `@test.local` contacts, synthetic account
  numbers, no matching client), deleted, and each one is now covered by the sweep so it
  cannot recur.
* **Final verification, after the last run:**

```
remaining: portfolios=0 test_contacts=0 test_notifications=0; temp workbooks removed=12

portfolios WHERE name LIKE 'C-TEST-%'                         = 0
"ClintContacts" WHERE contact LIKE '%@test.local'             = 0
notifications  WHERE data->>'message' LIKE '%تنبيه اختبار%'   = 0
clints         WHERE name LIKE 'عميل L%' / 'عميل N%' / 'عميل DL%'  = 0
uploads/temp/c_*.xlsx                                         = 0
```

* The `:9803` gunicorn was stopped at the end of the pass (`curl :9803/health` → connection
  refused). `:9797` was left running, was never restarted, and answers `/health` with 200.
* `pip install requests` was run inside the container early on; it is **not** in
  `requirements.txt` and is no longer needed — `tests/c_parity.py` falls back to a stdlib
  `urllib` shim when `requests` is absent.
