# Report center: self-service BI and SQL builder (plan)

Customer feedback 2026-11-04: "in report center add self BI page where user can build report by drag and drop, and add page with SQL UI builder with description of database fields".

Status: **approved 2026-11-04, built and deployed.** See §9 for the deploy record.

## 1. What the user gets

Two new items in the sidebar under **Recovery & team**, next to the Reports hub (مركز التقارير). Both pages work in Arabic RTL and English and use the Aidi Enterprise design system.

| Page | Route | Nav label (ar / en) | Who sees it |
|---|---|---|---|
| Self-service BI | `/reports/bi` | منشئ التقارير الذاتي / Self-service BI | new key `reports_bi` (on for ADMIN/SuperAdmin; admins can grant it to supervisors and collectors) |
| SQL builder | `/reports/sql` | منشئ استعلامات SQL / SQL builder | new key `reports_sql` (on for ADMIN/SuperAdmin only) |

The Reports hub also gets two cards that link to these pages.

### 1.1 Self-service BI (`/reports/bi`)

- **Pick a dataset:** Cases, Payments, Notes, Promises, Follow-ups, Calls.
- **Field list** on the side, split into **dimensions** (for example contract, portfolio, collector, case status, note status, payment status, payment method, and dates by day, month or year) and **measures** (for example case count, debt, approved collected, pending amount, note count, promised amount, call count, connected calls).
- **Drag and drop** fields into four zones: **Rows**, **Columns**, **Values** and **Filters**. Each field also has a "+" menu, so the page works by click, keyboard and touch too. Each zone can be reordered, and each chip can be removed.
- **Filters:** a date range plus value pickers for the dimensions (the same scoped pickers the reports use).
- **Result:** a pivot table with row and column totals, and a chart beside it: **bar, line or pie**, chosen with a toggle.
- **Save and load:** saved reports have a name. Private by default; "share with everyone who has this page" is a checkbox. You can rename and delete your own reports.
- **Export to Excel (.xlsx)** of the exact table shown. The file is RTL in Arabic and formula-safe.

### 1.2 SQL builder (`/reports/sql`)

- **Data dictionary panel:** every queryable table and field with its type, plus an **Arabic and an English description**, a search box, and the join keys between tables. Clicking a field inserts it.
- **Visual builder tab:** pick a base table, add joins (only the known relationships are offered), pick columns (with optional aggregates), add filters (`= ≠ > < between in contains is empty`), group by, order by and a row limit. The SQL it generates is shown live.
- **Raw SQL tab:** a SQL editor (monospace, line numbers, Ctrl+Enter runs). "Open in editor" copies the visual query there.
- **Results grid:** column types, row count, run time and a "truncated at N rows" warning. **Export to Excel** of the result.
- **Saved queries** and a "My recent runs" history.

## 2. Data layer: curated `bi` schema (migration 0093)

Users never query the raw tables. A new PostgreSQL schema `bi` holds **curated views** that expose only safe columns. These views are the dictionary, the visual builder's tables and the BI datasets.

| View | Source | Notes |
|---|---|---|
| `bi.cases` | `clints` + portfolio/contract | no `identityNumber`; account number, name, debt, status flags, owner, dates, last note time |
| `bi.payments` | `"Payments"` + `payment_reversals` | `status` = reversed / pending / rejected / approved (same rule as RPT-06), plus `counts_as_collected` (true only for approved, not deleted and not reversed) |
| `bi.notes` | `"Comments"` + status/reachability/method | note text is included (admins only, on the SQL page) |
| `bi.promises` | `"paymentDates"` + promise state | amount, due date (civil date), state |
| `bi.follow_ups` | `follow_ups` | due, done, late |
| `bi.calls` | `call_attempts` / wrap-ups | outcome; unknown duration stays NULL |
| `bi.users`, `bi.contracts`, `bi.portfolios`, `bi.branches`, `bi.comment_statuses`, lookups | admin tables | no password, identity, mobile, e-mail or target columns |

**Never exposed:** `call_dispatch_tokens`, `idempotency_keys`, `revoked_tokens`, `sip_credentials`, `sip_static_secrets`, `audit_events`, any token, secret, password or hash column, and employee personal fields.

The bilingual descriptions live in one catalog file (`CAWP-API/services/bi_catalog.py`). A test fails if a view column has no description, or if a description names a column that doesn't exist.

## 3. Security

**BI page (any user with `reports_bi`):**
- The SQL is built on the server from catalog identifiers only. No user text ever becomes SQL; values are bind parameters.
- The caller's **client scope** is always applied, through the existing `scope_sql()` rule on the case: a collector sees their own cases, a supervisor sees their users and contracts, SuperAdmin sees everything. A filter can only narrow it.
- Runs in a `READ ONLY` transaction with `statement_timeout` = 20 s, and at most 5,000 result groups.

**SQL page (`reports_sql`, admins only):**
- Arbitrary SQL can't be scoped by client, so the server also requires an **all-cases scope** (SuperAdmin). Anyone else gets 403 even if they hold the key.
- The query runs on a **separate database login, `alitqan_sql_reader`**. That login can read the `bi` views and nothing else: no `public` tables, no writes, no `CREATE`. PostgreSQL enforces this. Because it is its own login, the query cannot switch back to the app's role.
- Each run happens in a `READ ONLY` transaction with `statement_timeout` = 15 s and `idle_in_transaction_session_timeout`, and returns at most 10,000 rows (export up to 50,000).
- Only one statement is allowed. A lexer rejects `;` outside strings and comments, and only `SELECT` / `WITH … SELECT` are accepted.
- If the reader login isn't configured, the page says **"SQL runner not configured"** and refuses to run. It never falls back to the app's connection.

**Audit:** every BI run, SQL run and export is written to a new `report_builder_runs` table (user, page, SQL text or BI spec, row count, duration, outcome, error code) and to `audit_events`. Admins see the run history.

**Money:** "Approved collected" = approved, not deleted and not reversed. Pending, rejected and reversed money are separate measures and are never added to it. Date grouping uses Asia/Riyadh civil dates.

## 4. API (new blueprint `api/report_builder.py`, `core/api_response` conventions)

```
GET    /v2/bi/datasets                  datasets + dimensions/measures the caller may use
POST   /v2/bi/query                     {dataset, rows, columns, values, filters} -> pivot cells + totals
POST   /v2/bi/query/export              same body -> .xlsx (audited)
GET    /v2/bi/reports                   own + shared saved reports
POST   /v2/bi/reports                   save    (Idempotency-Key)
PUT    /v2/bi/reports/<id>              rename / update / share (revision check)
DELETE /v2/bi/reports/<id>              own only

GET    /v2/sql/dictionary               tables, columns, types, ar/en descriptions, joins
POST   /v2/sql/build                    visual spec -> SQL text (validated against the catalog)
POST   /v2/sql/run                      {sql} -> columns, rows, truncated, ms (audited)
POST   /v2/sql/run/export               {sql} -> .xlsx (audited)
GET/POST/PUT/DELETE /v2/sql/queries     saved queries
GET    /v2/report-builder/runs          run history (own; all for admins)
```

New tables (migration 0093): `bi_saved_reports`, `sql_saved_queries`, `report_builder_runs`, plus the `bi` schema views and permission keys `reports_bi` and `reports_sql`.

## 5. Frontend

- `CAWP-frontend/src/views/reportBuilder/bi/*` (field list, drop zones, pivot table, chart) and `views/reportBuilder/sql/*` (dictionary, visual builder, editor, grid).
- **No new npm packages:** native HTML5 drag and drop and ApexCharts (already used). Excel export is built on the server, so the browser needs no spreadsheet library. The editor is a styled textarea with line numbers.
- The page state (dataset, zones, chart type, active tab) lives in the URL hash (`useHashState`), so Back and shared links keep it.
- Locales: new `rb.*` blocks only, added to `ar.json` and `en.json` without reformatting the files.

## 6. Tests (pytest on the throwaway PostgreSQL, port 5439)

- **BI:** a collector sees only their own cases; a supervisor sees their scope; a filter can't widen scope; unknown field → 422; "approved collected" excludes pending, rejected and reversed; Riyadh day grouping; save/load/share/delete permissions; export returns a valid .xlsx.
- **SQL:** a non-admin gets 403; a supervisor holding `reports_sql` gets 403; `INSERT`, `UPDATE`, `DROP` and `;`-chained statements are refused; a direct read of `public.sip_credentials`, `users` or `call_dispatch_tokens` fails with permission denied; `set_config('role', …)` can't escalate; a timeout returns 408/422 with a code; the row cap sets `truncated`; every run writes `report_builder_runs`.
- **Catalog:** every view column has ar/en descriptions, and no secret column appears in any `bi` view.

## 7. Deploy (after approval and green tests)

1. Sync the code, run `manage_db.py up` (0093: schema `bi`, views, tables, keys). This is additive and takes seconds.
2. **One-time production step, needs your OK:** as `postgres`, create the login `alitqan_sql_reader` with a generated password. Grant it `USAGE` on `bi` and `SELECT` on its views only. Put its URL in the secret file `/etc/alitqan/sql_reader.url` (mode 0640, root:cawp), which the API reads on every run, so no restart is needed. `SQL_READER_DATABASE_URL` in the env file also works, but needs a restart. The migration grants the views to the role automatically if the role already exists. If it doesn't exist yet, the migration skips the grant, so it is safe to run first.
3. HUP the API, build the frontend, and rsync it.
4. Verify on the server as an admin and as a collector: both pages load in ar and en; BI totals match the Reports hub (RPT-06/RPT-08) for the same dates; the SQL page refuses writes and secret tables; the export opens in Excel.

## 8. Decisions for you

1. **Who gets the SQL page.** Proposed: SuperAdmin/ADMIN only, because arbitrary SQL can't respect client scope. The other option is a scoped SQL page for supervisors, which needs row-level security on every view. That is a bigger change, so I'd do it later if needed.
2. **Who gets the BI page by default.** Proposed: ADMIN/SuperAdmin on. Admins then turn on `reports_bi` per role in Roles. It is always scoped.
3. **Note text in BI.** Proposed: notes can be counted and grouped in BI, but the free text shows only on the admin SQL page.
4. **The production read-only login (§7.2).** This is a new database role on the production PostgreSQL, so I'll ask you again right before creating it.

## 9. Deploy record (2026-11-04)

- Tests: `tests/test_report_builder.py` 14 passed; full API suite 1633 passed, 52 skipped (PBX/CDR fixtures), on the throwaway cluster (port 5439, prefix `cawp_test_rb`).
- Migration 0093 was applied on production at 05:24 UTC by a sibling session's deploy, with the same checksum as this file set.
- Backend files and frontend deployed 05:35–05:36 UTC; API reloaded with HUP; `report_builder` blueprint registered (18 routes).
- `alitqan_sql_reader` created (user approval): reads the 11 `bi` views and 0 of the 123 `public` tables; no superuser, role or database rights. It needed an explicit `GRANT CONNECT` because production revokes CONNECT from PUBLIC; the ops script now includes it. The URL is in `/etc/alitqan/sql_reader.url` (0640 root:cawp).
- `reports_bi` and `reports_sql` switched on for ADMIN and SuperAdmin (user approval, `audit_events` action `role.permissions.update`). The deploy's merge had set them on at 05:24; something later set them off with no audit row.
- Verified on production data: BI, the SQL runner and the RPT-08 rule give the same October 2026 approved total (16,091,413.52 SAR). Writes, secret tables, chained statements, a data-modifying CTE and `set_config('role', …)` are all refused; a 2 s timeout fires; the 10,000-row cap applies.
- Not done: a check of both pages in a browser (no login was available to the agent).
