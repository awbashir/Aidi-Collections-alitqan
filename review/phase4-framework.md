# Phase 4, stream R0: the v2 report framework and RPT-06/07/08

Date: 2026-09-24. Branch `phase-4-reports`, not committed. Scope: stream R0 in `docs/phase4-spec.md`.
**Migrations 0039–0040 were not applied to `cawp-db`**, and no container was restarted. Every
measurement on `cawp-db` ran in read-only transactions: `default_transaction_read_only=on`
on the connection, plus the framework's `REPEATABLE READ READ ONLY` snapshot. No test or
measurement wrote to `cawp-db`.

## 1. Files

| Area | Files |
|---|---|
| Framework | `CAWP-API/core/reports/`:<br>• `definition.py`: the interface (filters, columns, sorts, totals, `ReportDefinition`)<br>• `registry.py`: discovers `services/reports_v2_*.py`<br>• `engine.py`: context, validation, SQL composition, snapshot, preview<br>• `options.py`: scoped selectors<br>• `privacy.py`: protected employee fields<br>• `exports.py`: export API side and worker side<br>• `writers.py`: CSV, XLSX and Arabic XLSX<br>• `xlsx.py`: streaming SpreadsheetML writer<br>• `settings.py`: `REPORT_*`<br>• `perf.py`: reconciliation and timing harness, read-only |
| API | `CAWP-API/api/reports_v2.py`: blueprint `reports_v2`, 8 rules. It was already listed in `api/__init__.py`. |
| Reference reports | `CAWP-API/services/reports_v2_collections.py`: RPT-06, RPT-07 and RPT-08 |
| Worker | `CAWP-API/worker/main.py`: export claim loop next to the bulk jobs, alternating between the two queues. `Heartbeat` now takes a `renew` function. |
| Retention | `CAWP-API/core/bulk/retention.py`: also purges expired report files. It skips this step when the table is missing. |
| Migrations | `db/migrations/0039_report_exports.sql` and `0040_report_export_attempts.sql`. Both create new tables only and run instantly. **0041 is not used** (see §10). |
| Tests | `CAWP-API/tests/test_reports_v2_support.py` (fixtures and smoke), `test_reports_v2_framework.py`, `test_reports_v2_exports.py`, `test_reports_v2_collections.py`, `tests/fixtures/seed_reports.sql` |
| Docs | `CAWP-API/DEPLOY.md` (new §5c) and this report |

Not touched:
- legacy `api/reports.py`, `app.py`, `models.py`;
- `core/permissions.py`: no new key is needed;
- `conftest.py`, `seed.sql`, `docker-compose.yml`, the frontend;
- `core/bulk/*` apart from `retention.py`.

## 2. Design and decisions

- **One definition drives everything.** The preview, the totals, the selectors and the export all use the same `engine.compose()`:
  - the definition's `build(ctx)` supplies the FROM clause, the current case scope and the fixed rules;
  - the framework adds each filter's own SQL.

  The only difference between preview and export is LIMIT/OFFSET.
- **Snapshot.** A preview runs its totals and its page in one `REPEATABLE READ READ ONLY` transaction on a dedicated connection (`engine.snapshot()`). The page and the exact totals therefore always agree. A test commits a row between the two queries, and neither query sees it.
- **Stable paging.** Every sort ends with the definition's `id_sql`, in the same direction. With 110 fully tied rows, six pages of 20 give the same order as one page of 200, with no row lost or repeated.
- **Scope.** The **current case scope** (`services/clients_scope.py`: SuperAdmin all, supervisor = supervised users' cases OR supervised contracts' portfolios, collector = own cases) is always part of `build()`.
  - Filters only narrow it.
  - A creator id outside the scope returns nothing.
  - A case moved out of a supervisor's contracts disappears from that supervisor's RPT-06/08.

  This is an intentional correction versus the legacy recorder-based rule (§7).
- **RPT-07 is the exception (lead decision, 2026-09-24).** The catalogue defines the "my …" reports by the recorder (RPT-05 "Notes recorded by the current user, irrespective of current case ownership"; RPT-07 "Collections recorded by the current collector"). So RPT-07 is **recorder = me AND approved AND the date window, with no case-scope intersection**: it lists the caller's own recorded work whatever the case's current owner, which reveals nothing the caller did not enter. Its `build()` deliberately omits `ctx.scope_sql()`; protected-field redaction still applies to other users' fields (RPT-07 has none).
- **Validation.** Unknown parameters, bad dates, reversed ranges, unknown enum values, bad uuids, too many ids, over-precise, negative or too-large amounts, text over 100 characters, a bad sort and bad paging all return **422 `validation_failed`**, with `error = {param: code}` listing *every* bad parameter at once. The codes are `invalid`, `required`, `reversed_range`, `range_too_long`, `too_long`, `too_many`, `unknown_value`, `negative`, `too_precise`, `too_large` and `unknown_parameter`.
- **Dates.**
  - `DateRange` (payment and promise dates) is an inclusive civil date range in Asia/Riyadh. On a `timestamptz` column it becomes the half-open UTC range `[Riyadh 00:00 of from, Riyadh 00:00 after to)`, so `ix_payments_payment_date`-style indexes stay usable.
  - `TimestampRange` (notes, CDR) uses the same Riyadh day bounds.
- **Money.** Amounts are `round(x::numeric, 2)` in SQL and exact strings in JSON and CSV (`"1234.50"`). In XLSX they are numbers with the exact decimal text and format `#,##0.00`. `original_debt` is per-row context and is never summed.
- **Protected employee fields.** A column flagged `protected="identity"|"target"` with a `subject` column is nulled unless the viewer holds `users_details`/`users_edit` (targets: also `users_edit_target`) or it is the viewer's own row. This follows `core/user_privacy.py`. The worker reads the requester's rights fresh from the database. RPT-06/07/08 contain no protected field; a test-only report proves the mechanism.
- **CSV decision:** UTF-8 **with BOM**, CRLF, same as the Phase 2 artifacts.
  - The legacy users open exports in Excel, which garbles Arabic without the BOM.
  - The header is the column keys, and the values are exactly the preview JSON values (money strings, `YYYY-MM-DD`, ISO UTC `Z`, enum codes).
  - Formula safety uses `core.bulk.artifacts.safe_cell`: a text or enum cell starting with `= + - @ TAB CR` gets a leading `'`. Generated columns (money, dates, ids) are never altered.
- **XLSX decision (a deviation from "openpyxl write-only").**
  - The image has no `lxml`, so openpyxl's write-only mode falls back to `et_xmlfile`. Its "incremental" writer keeps the whole `<sheetData>` tree in memory until the sheet closes.
  - Measured on `cawp-db` for 23,090 rows: **355 MB of Python heap and 32 s**.
  - `core/reports/xlsx.py` is a ~200-line streaming SpreadsheetML writer instead. It streams one XML string per row to a temp file, then zips the parts. Result: **2.3 MB and 1.6 s**.
  - Text cells are typed inline strings; no `<f>` element is ever written. A CR is written as `&#13;` and other control characters as `_xHHHH_`, so the text round-trips.
  - openpyxl is still used to read the files back in the tests: RTL sheet views, frozen header, types and number formats.
  - If `lxml` is added to the image later, switching back is a writer-only change.
- **Arabic RTL XLSX (`xlsx_ar`).** Both sheets are `rightToLeft`, with Arabic headers and Arabic enum and boolean values. The Arabic sheet names are "تحصيلات المستخدمين" / "تحصيلاتي" / "السدادات" (after the legacy exports) and "الملخص". The summary sheet has the report, the "البيانات حتى" cutoff (Riyadh time), the requester, the filters (Arabic labels), the row count and the exact totals.
- **Exports are the worker's job.**
  - The export uses a fresh snapshot, so a later export may differ from an earlier preview (tested).
  - It streams from a server-side cursor in batches of 500.
  - The totals come from the same snapshot, and the written row count must equal the snapshot count.
  - The permission is read fresh from `roles`, together with `users.is_active`, at the **start** and at **publication**. The scope is re-resolved with the cache dropped.
  - Every commit is conditional on `locked_by = <worker>`.
  - Artifacts are private (`BULK_STORAGE_DIR/reports/…`, mode 0600) and **creator-only**. Another user gets 404 `export_not_found`, the same as a missing export.
  - The download rechecks the report key.
  - Artifacts expire after 7 days, and retention then deletes the file.
  - Audit rows: `report_export.create|publish|fail|download|purge`.

## 3. The definition interface (for streams R-A, R-B, R-C)

Add `services/reports_v2_<stream>.py` with `REPORTS = (...)`. Discovery imports it on first use, so no framework file changes. A minimal report, the case list RPT-02 sketch:

```python
from core.reports import Built, Column, DateRange, Enum, Ids, ReportDefinition, Sort, Text, Total

class ActiveCases(ReportDefinition):
    id = "RPT-02"
    family = "cases"
    label_en, label_ar = "Unarchived cases", "العملاء غير المؤرشفين"
    description_en, description_ar = "…", "…"
    permissions = ("clints",)                 # the legacy screen's key (screen-key rule); any-of
    legacy = {"endpoint": "GET /clints", "screen": "/clients", "key": "clints"}
    fixed = ({"en": "Unarchived only", "ar": "غير المؤرشفين فقط"},)
    xlsx_ar = False                           # True offers the Arabic RTL XLSX
    id_sql = "c.id"                           # unique row id: the stable tie-breaker
    columns = (
        Column("case_id", "Case ID", "معرف العميل", "uuid", "c.id"),
        Column("account_number", "Account", "رقم الحساب", "text", 'c."accountNumber"'),
        Column("debt", "Original debt", "المديونية", "money", "round(c.amount::numeric, 2)"),
        Column("owner_id", "Owner id", "", "uuid", "c.user_id", hidden=True),
        Column("owner_name", "Current owner", "المحصل الحالي", "text",
               "btrim(concat_ws(' ', ou.frist_name, ou.last_name))", role="owner"),
        Column("owner_mobile", "Owner mobile", "جوال المحصل", "text", "ou.mobile",
               protected="identity", subject="owner_id"),        # employee field: user_privacy rules
        Column("created_at", "Created", "تاريخ الإنشاء", "datetime", "c.create_at"),
    )
    filters = (
        DateRange("created", "Created", "تاريخ الإنشاء", "c.create_at"),       # created_from / created_to
        Ids("contract", "Contract", "العقد", "contract", "pf.contract_id"),
        Ids("portfolio", "Portfolio", "المحفظة", "portfolio", "c.portfolios_id", depends_on=("contract",)),
        Ids("owner", "Owner", "المحصل", "user", "c.user_id", depends_on=("contract", "portfolio")),
        Enum("locked", "Lock", "القفل", {"yes": ("Locked", "مقفل"), "no": ("Unlocked", "غير مقفل")},
             predicates={"yes": "c.is_locked", "no": "NOT c.is_locked"}),
        Text("q", "Search", "بحث", ("c.name", 'c."accountNumber"', 'c."identityNumber"')),
    )
    sorts = (Sort("created_at", "Created", "تاريخ الإنشاء", "c.create_at"),
             Sort("debt", "Debt", "المديونية", "c.amount"))
    default_sort = "-created_at"
    totals = (Total("debt_total", "Total debt", "إجمالي المديونية", "coalesce(sum(round(c.amount::numeric, 2)), 0)", "money"),
              Total("locked_count", "Locked", "المقفلة", "count(*) FILTER (WHERE c.is_locked)"))

    def build(self, ctx):
        scope, params = ctx.scope_sql("c")    # current case scope; never omit it
        return Built("""clints c
                        LEFT JOIN portfolios pf ON pf.id = c.portfolios_id
                        LEFT JOIN users ou ON ou.id = c.user_id""",
                     ["c.is_archive = false", scope], params)

REPORTS = (ActiveCases(),)
```

Rules and conventions:

- **`build(ctx)`** returns `Built(from_sql, where, params)`.
  - The `where` list must contain the scope (`ctx.scope_sql(alias)`) and the fixed rules. Filters are added by the framework.
  - The one exception is a "my recorded …" report (RPT-05, RPT-07): the lead decided these are recorder = me with no case-scope intersection. See `MyApprovedCollections.build()`.
  - `ctx` has `actor_id`, `scope`, `filters` (normalized), `purpose` (`rows`, `options` or `export`) and `value(key)`.
  - One FROM serves the totals, the page and the export. PostgreSQL removes unused `LEFT JOIN`s to unique keys.
- **Bind parameters** are named. The prefixes `f_`, `rq_` and `sc_` are the framework's; use your own prefix, e.g. `rpt07_me`.
- **Filters:**
  - `DateRange(key, …, column, column_type="timestamptz"|"date", max_days=None, required=False)` takes the params `<key>_from` and `<key>_to`.
  - `TimestampRange(...)` is the same over event timestamps.
  - `Enum(key, …, choices={code: (en, ar)}, column=… | predicates={code: sql}, multi=True, default=None)`.
  - `Ids(key, …, entity, column, depends_on=(), sql_template=None)`. Its selector is `/v2/report-options/<key>`. The candidates are `DISTINCT column` over your base plus the `depends_on` filters. Entities: `user`, `branch`, `contract`, `portfolio`; add others with `options.register_entity(Entity(...))`.
  - `Text(key, …, columns)` is a literal, case-insensitive "contains" (escaped `ILIKE`, pg_trgm friendly).
  - `AmountRange(key, …, column)` takes exact SAR bounds `<key>_from`/`<key>_to`.
- **Columns:**
  - `Column(key, en, ar, type, sql, enum=None, protected=None, subject=None, hidden=False, export=True, role=None)`.
  - Types: `text`, `int`, `money`, `date`, `datetime`, `enum`, `bool`, `uuid`, `json`.
  - `role` documents the four roles (`owner`, `recorder`, `approver`, `historical_owner`).
  - Money must be `money`, which serializes as a string.
- **Totals:** `Total(key, en, ar, aggregate_sql, "int"|"money")` covers the full filtered result; `count` is always added.
- **Sorts:** `Sort(key, en, ar, sql, nulls_last=True)`. The request uses `sort=key` or `sort=-key`, and `id_sql` is always the last key.
- **Validation at registration:**
  - unique column keys;
  - `subject` must be a column;
  - no duplicate or reserved params (`page`, `page_size`, `sort`, `report`, `selected`, `format`);
  - `depends_on` must name filters;
  - the default sort must exist;
  - totals must be unique and not `count`.
- **Latest-note style reports (RPT-04 `latest_matching_per_case`).** Put the selection in `from_sql` as a subquery or `LATERAL` that applies the matching filters. Because filters are appended outside the FROM, filters that must act *inside* the selection should read `ctx.value(key)` in `build()`. Declare them with a filter whose `sql()` returns `""`, i.e. a small subclass.
- **Tests:** copy `tests/test_reports_v2_support.py`, which provides the `rc` client, `run_export` and SQL helpers, and create your own portfolio or contract per test.

## 4. Endpoints (real responses from the test suite; ids shortened where marked …)

Envelope: `{message, status, data}`; failures are `{message, status:false, error, code}`. All endpoints need a JWT.

| Method, path | Permission | Parameters | Success |
|---|---|---|---|
| `GET /v2/reports` | JWT | – | 200 `{items[{id, family, labels, description, permissions, formats}], total, families[{family, reports[]}]}`: only the reports whose key the caller holds |
| `GET /v2/reports/<id>` | report key | – | 200 definition; 403 `forbidden {required_any}`; 404 `report_not_found`. Ids are case-insensitive. |
| `GET /v2/reports/<id>/rows` | report key | filter params, `sort`, `page` (1-based), `page_size` (default 50, max 200) | 200 `{items, total, totals, page, page_size, pages, sort, filters, params, snapshot_at, report}` |
| `GET /v2/report-options/<field>` | key of `report` | `report` (required), `q`, `page`, `selected=<id>[,…]`, plus any of the report's params (only `depends_on` ones apply) | 200 `{items[{id,label,secondary,active}], total, page, page_size: 50, pages, selected[], depends_on, entity, field, q, report}`; 404 `unknown_option_field` |
| `POST /v2/reports/<id>/exports` | report key, `Idempotency-Key` | `{format: csv\|xlsx\|xlsx_ar, params: {…same as rows…}, sort}` | **202** export (`queued`); a replay returns the same bytes with `Idempotent-Replayed: true` |
| `GET /v2/report-exports` | JWT (own only) | `report`, `status` (`queued, running, retrying, succeeded, failed, expired`), `page`, `page_size` (max 100) | 200 `{items, total, page, page_size, filters}`, newest first |
| `GET /v2/report-exports/<id>` | creator | – | 200 export plus `attempts[]`; others get 404 `export_not_found` |
| `GET /v2/report-exports/<id>/download` | creator plus the current report key | – | 200 file (`Cache-Control: private, no-store`); 409 `export_not_ready` / `export_failed`; 410 `export_expired` / `export_purged`; 403 `forbidden` |

Examples (real test output, abridged):

```
GET /v2/reports                                   (r.sup: supervisor, the three collection keys)
200 {"data": {"items": [
   {"id": "RPT-06", "family": "collections", "labels": {"en": "All collections", "ar": "تحصيلات المستخدمين"},
    "description": {"en": "Every payment on cases in your current scope, in every status …", "ar": "…"},
    "permissions": ["collections_users"], "formats": ["csv", "xlsx", "xlsx_ar"]},
   {"id": "RPT-07", …, "labels": {"en": "My approved collections", "ar": "تحصيلاتي"}, "permissions": ["collections_currentUser"]},
   {"id": "RPT-08", …, "labels": {"en": "Approved collections", "ar": "السدادات المعتمدة"}, "permissions": ["accounts_payments"]}],
  "total": 3, "families": [{"family": "collections", "reports": ["RPT-06", "RPT-07", "RPT-08"]}]}, "message": "ok", "status": true}

GET /v2/reports/RPT-06
200 {"data": {"id": "RPT-06", "family": "collections", "permissions": ["collections_users"], "formats": ["csv","xlsx","xlsx_ar"],
  "default_sort": "-created_at", "paging": {"default_page_size": 50, "max_page_size": 200},
  "fixed": [{"en": "Cases in your current scope only", "ar": "العملاء ضمن نطاقك الحالي فقط"}],
  "legacy": {"endpoint": "GET /payments, GET /payments/export", "screen": "/user_collection", "key": "collections_users"},
  "filters": [
   {"key": "payment_date", "type": "date_range", "params": ["payment_date_from", "payment_date_to"],
    "semantics": "inclusive_civil_dates", "timezone": "Asia/Riyadh", "labels": {"en": "Payment date", "ar": "تاريخ الدفع"}, …},
   {"key": "status", "type": "enum", "multi": true, "params": ["status"],
    "choices": [{"value": "pending", "labels": {"en": "Pending approval", "ar": "بانتظار الاعتماد"}},
                {"value": "approved", "labels": {"en": "Approved", "ar": "معتمدة"}}, {"value": "rejected", …}, {"value": "reversed", …}]},
   {"key": "creator", "type": "ids", "selector": {"field": "creator", "entity": "user", "endpoint": "/v2/report-options/creator"},
    "depends_on": ["creator_branch", "contract", "portfolio"], "max_ids": 200, …},
   {"key": "creator_branch", …}, {"key": "contract", …},
   {"key": "portfolio", "type": "ids", "depends_on": ["contract"], "selector": {"endpoint": "/v2/report-options/portfolio", …}},
   {"key": "amount", "type": "amount_range", "params": ["amount_from", "amount_to"], "semantics": "inclusive_exact_sar"},
   {"key": "account", "type": "text", "semantics": "literal_contains_case_insensitive", "max_len": 100},
   {"key": "q", "type": "text", …}],
  "columns": [{"key": "payment_id", "type": "uuid", …}, {"key": "status", "type": "enum", "enum": [ … ]},
              {"key": "amount", "type": "money", "money": true, "currency": "SAR", "labels": {"en": "Amount", "ar": "المبلغ"}}, …],
  "sorts": [{"key": "created_at", "labels": {…}}, {"key": "payment_date", …}, "amount", "approval_at", "account_number",
            "customer_name", "recorder_name"],
  "totals": [{"key": "count", "type": "int"}, {"key": "recorded_amount", "type": "money", "currency": "SAR",
             "labels": {"en": "Recorded, all statuses (not recovery)", "ar": "إجمالي المسجل (ليس تحصيلاً)"}}, …]}}

GET /v2/reports/RPT-06/rows?portfolio=b6ed…&payment_date_from=2026-09-01&payment_date_to=2026-09-30&page_size=2
200 {"data": {"report": "RPT-06", "total": 3, "page": 1, "page_size": 2, "pages": 2, "sort": "-created_at",
  "snapshot_at": "2026-09-24T01:34:17.253322Z",
  "totals": {"count": 3, "recorded_amount": "1650.75", "approved_amount": "1200.00", "approved_count": 1,
             "pending_amount": "300.50", "pending_count": 1, "rejected_amount": "0.00", "rejected_count": 0,
             "reversed_amount": "150.25", "reversed_count": 1, "case_count": 1},
  "filters": {"payment_date": {"from": "2026-09-01", "to": "2026-09-30"}, "portfolio": ["b6ed…"], "status": null,
              "creator": null, "creator_branch": null, "contract": null, "amount": null, "account": null, "q": null},
  "params": {"payment_date_from": "2026-09-01", "payment_date_to": "2026-09-30", "portfolio": "b6ed…"},
  "items": [
   {"payment_id": "68fe…", "status": "pending", "amount": "300.50", "payment_date": "2026-09-10",
    "created_at": "2026-09-10T08:00:00Z", "recorder_name": "محصل التقارير", "recorder_user_name": "r.col1",
    "recorder_branch": "فرع التقارير الرياض", "case_id": "ba81…", "account_number": "ACC-EX-0001",
    "customer_name": "عميل مثال", "identity_number": "1012345678", "contract_name": "Reports contract A",
    "portfolio_name": "Example portfolio", "original_debt": "5000.00", "owner_name": "محصل التقارير",
    "collection_method": "Transfer", "collection_type": "Partial", "approval_at": null, "approval_by_name": null,
    "approval_comment": null, "reversed_at": null, "reversed_by_name": null, "reversal_reason": null},
   {"payment_id": "ca36…", "status": "reversed", "amount": "150.25", "payment_date": "2026-09-05", …,
    "approval_at": "2026-03-11T08:00:00Z", "approval_by_name": "Report Supervisor",
    "reversed_at": "2026-09-24T01:34:17.147135Z", "reversed_by_name": "Report Supervisor", "reversal_reason": "bounced cheque"}]}}

GET /v2/reports/RPT-06/rows?payment_date_from=2026-09-30&payment_date_to=2026-09-01&status=paid
422 {"code": "validation_failed", "error": {"payment_date_to": "reversed_range", "status": "unknown_value"},
     "message": "invalid report parameters", "status": false}
GET /v2/reports/RPT-06/rows                         (a collector)
403 {"code": "forbidden", "error": {"required_any": ["collections_users"]}, "message": "You are not authorized to access this resource", "status": false}

GET /v2/report-options/portfolio?report=RPT-06&contract=e5e5…0301&q=Example&selected=b6ed…
200 {"data": {"report": "RPT-06", "field": "portfolio", "entity": "portfolio", "q": "Example",
  "depends_on": {"contract": ["e5e5…0301"]}, "total": 1, "page": 1, "page_size": 50, "pages": 1,
  "items": [{"id": "b6ed…", "label": "Example portfolio", "secondary": "Reports contract A", "active": true}],
  "selected": [{"id": "b6ed…", "label": "Example portfolio", "secondary": "Reports contract A", "active": true}]}}

POST /v2/reports/RPT-06/exports      Idempotency-Key: 3f0c1f5e-…
{"format": "xlsx_ar", "params": {"portfolio": "b6ed…", "payment_date_from": "2026-09-01", "payment_date_to": "2026-09-30"}, "sort": "-amount"}
202 {"data": {"id": "75ee…", "report": {"id": "RPT-06", "family": "collections", "labels": {…}}, "format": "xlsx_ar",
  "status": "queued", "stored_status": "queued", "requested_by": "e5e5…0502", "requested_at": "2026-09-24T01:34:17.293783Z",
  "definition": {"version": 1, "filters": {…normalized…}, "params": {…flat…}, "sort": "-amount",
                 "columns": ["payment_id", "status", "amount", "payment_date", "created_at", "recorder_name", …, "reversal_reason"]},
  "progress": {"rows_written": 0, "rows_expected": null, "percent": 0.0}, "row_count": null, "totals": null,
  "artifact": null, "attempt": 0, "max_attempts": 3, "failed_attempts": 0, "error": null,
  "started_at": null, "cutoff_at": null, "finished_at": null, "actions": {"download": false}}, "message": "export queued"}

GET /v2/report-exports/75ee…                      (after the worker ran)
200 {"data": {…, "status": "succeeded", "started_at": "…17.304574Z", "cutoff_at": "2026-09-24T01:34:17.316720Z",
  "finished_at": "…17.342233Z", "row_count": 3, "progress": {"rows_written": 3, "rows_expected": 3, "percent": 100.0},
  "totals": {"count": 3, "recorded_amount": "1650.75", "approved_amount": "1200.00", …, "reversed_amount": "150.25", …},
  "artifact": {"file_name": "RPT-06-20260924-0434-75ee3979-ar.xlsx",
               "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
               "size_bytes": 4587, "sha256": "b24c…", "expires_at": "2026-10-01T01:34:17.342233Z", "purged_at": null,
               "available": true},
  "attempts": [{"attempt": 1, "worker_id": "test-worker", "outcome": "completed", "rows_written": 3,
                "started_at": "…", "finished_at": "…", "error": null}],
  "actions": {"download": true}}}

GET /v2/report-exports/75ee…/download
200  Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
     Content-Disposition: attachment; filename=RPT-06-20260924-0434-75ee3979-ar.xlsx
     Cache-Control: private, no-store      X-Content-Type-Options: nosniff
GET /v2/report-exports/75ee…/download   (another user)
404 {"code": "export_not_found", "error": {"id": "75ee…"}, "message": "report export not found", "status": false}
```

Frontend notes:

- Build the filter form from `filters[]`. Send the flat `params` (the rows response echoes both `filters` and `params`) and store `params`, `sort` and `page` in the URL hash.
- Selectors call `/v2/report-options/<key>?report=<id>` with the current values of `depends_on`, plus `selected=<ids>` on reload to resolve labels beyond page one.
- Poll `GET /v2/report-exports/<id>` every 1–2 s while `status` is `queued`, `running` or `retrying`, and show `progress`.
- **The export body takes the same `params` as the rows request**; paging parameters are ignored.

## 5. Exports: schema, lifecycle, worker

- **`report_exports` (0039).** Columns:
  - `report_id`, `format` (`csv`, `xlsx`, `xlsx_ar`), `status`;
  - `definition` jsonb (version, report, format, normalized filters, flat params, sort, columns) and its sha256;
  - `requested_by`, `requested_at`, `started_at`, `cutoff_at`, `finished_at`;
  - `rows_expected`, `rows_written`, `row_count`, `totals` jsonb;
  - `file_name`, `stored_path`, `content_type`, `size_bytes`, `sha256`, `expires_at`, `purged_at`;
  - `attempt`, `max_attempts`, `failed_attempts`, `locked_by`, `lease_until`, `heartbeat_at`, `error_code`, `error_message`, `updated_at`.

  CHECKs:
  - `running` ⇔ lease;
  - terminal ⇔ `finished_at`;
  - `succeeded`/`expired` ⇒ artifact and metadata;
  - `expired` ⇔ `purged_at`;
  - `failed` ⇒ `error_code`.

  Indexes: claimable, leased, requester and expiry.
- **`report_export_attempts` (0040).** One row per claim. `outcome` is `running`, `completed`, `lease_expired`, `error`, `interrupted` or `failed`.
- **Lifecycle:**
  - `queued` → `running` → `succeeded` → `expired`.
  - `running` → `retrying` when the lease expires or on SIGTERM (not counted) → `running` again, …
  - `running` → `failed` on `permission_denied`, `too_many_rows`, `artifact_too_large`, `unknown_report`, `invalid_definition`, `row_count_mismatch`, `internal_error` (at max attempts) or `max_attempts_exceeded`.
  - The API shows a `succeeded` export past `expires_at` as `expired` before retention runs.
- **Worker** (`worker/main.py`):
  - Each loop iteration recovers the expired leases of both queues and claims from one queue. The queue tried first alternates after each piece of work.
  - The export queue is skipped while the table is missing, checked every 60 s. So a worker restarted before 0039 is applied keeps running the bulk jobs.
  - The heartbeat thread renews the export lease; each batch also renews it and records progress.
- **Real process check** (a scratch database on the test server, dropped afterwards; not `cawp-db`):
  - `python -m worker` was killed with **SIGKILL** at 860 of 3,000 rows (1-row batches, lease 5 s).
  - A second worker logged `lease expired -> retrying`, claimed attempt 2 and published 3,000 distinct rows in 8.6 s. The attempts were `[(1, lease_expired), (2, completed, 3000)]`.
  - SIGTERM then made the worker exit with code 0.

## 6. Reference reports RPT-06, RPT-07, RPT-08

**Keys.** Evidence: `CAWP-frontend/src/configs/routes.config/routes.config.ts` (the route `key`s), `configs/navigation.config/index.ts`, and `views/permissions/form/Fields.tsx` (the role editor's labels).

| Report | Legacy endpoint (API) → screen | Key | v2 inclusion |
|---|---|---|---|
| RPT-06 all collections | `GET /payments` (+ `/payments/export`) → `/user_collection` (`views/collections/users`, "تحصيلات المستخدمين") | `collections_users` | current case scope; every status unless `status` is given |
| RPT-07 my approved collections | `GET /payments/user` (+ export) → `/current_user_collection` (`views/collections/currentUser`, "تحصيلاتي") | `collections_currentUser` | recorder = me AND approved, whatever the case's current owner (no case-scope intersection; lead decision) |
| RPT-08 approved collections | `GET /payments/approval_status` (+ export) → `/payment` (`views/accounts`, Accounting "السدادات", key `accounts_payments` = "عرض") | `accounts_payments` | current case scope AND approved (reversed excluded); not the pending desk |

On `cawp-db`, 253 collectors (role محصل) hold only `collections_currentUser`. Supervisors, managers, operations and ADMIN hold all three keys.

- **Status:**
  - `reversed` means a `payment_reversals` row exists.
  - Otherwise, `approval_status` NULL is `pending`, true is `approved` and false is `rejected`.
  - A legacy-deleted payment that was never reversed is excluded; `cawp-db` has none.
  - A reversed payment keeps its approval fields and counts only in `reversed_*`.
- **Columns:**
  - payment id and status;
  - amount, payment date (Riyadh civil), entered at;
  - recorder: name, login, branch;
  - case id, account, customer, customer identity;
  - contract, portfolio;
  - original debt (per row, never summed);
  - current case owner;
  - method and type;
  - approval at, approval by, approval comment;
  - reversal at, reversal by, reversal reason;
  - RPT-08 also has `provision`, the case attribute "البروفجن" exported by the legacy accounting screen (null when absent; the legacy export wrote 0).
- **Filters:**
  - payment date (inclusive civil);
  - status (RPT-06 only);
  - creator (RPT-06/08) and creator branch (the recorder's branch, kept separate from the contract);
  - contract, portfolio (depends on contract);
  - amount range, account (literal contains), and `q` (customer name, account or identity).
- **Totals (all three reports):**
  - `count`;
  - `recorded_amount` (not recovery);
  - `approved_amount`/`_count`, `pending_…`, `rejected_…`, `reversed_…`;
  - `case_count`.
- **Sorts:** entered at (default `-created_at`, as in legacy), payment date, amount, approval date, account, customer, recorder.
- **The Arabic XLSX** follows the legacy exports: they were client-side ExcelJS files with `rightToLeft` and Arabic headers (`components/shared/myComponents/exportPayment.tsx`, `export2.tsx`). The legacy status column labelled pending payments as "مرفوض"; v2 uses the correct Arabic status labels.

## 7. Reconciliation with the legacy endpoints on `cawp-db`

Method (`python -m core.reports.perf reconcile`, read-only):

- For each actor and window, the **real legacy handlers** (`api/reports.py` in-process, with a JWT for the actor) give `countRow` and, for RPT-07/08, `totalAmount`.
- An SQL replica of their predicates rebuilds the legacy row set. **The replica equalled `countRow` in all 84 comparisons.**
- The v2 report runs for the same window, and every differing row is categorized.
- **Result: 84 comparisons; every difference falls in one of four named categories (`other` = 0); and in every row, legacy Σ − legacy-only rows + v2-only rows = v2 `recorded_amount` to the halala.**

Categories:

| Code | Meaning | Rows over all 84 runs |
|---|---|---:|
| `+first-day` | **Inclusive civil dates.** The payment's civil date is the window's first day, but it is stored at Riyadh midnight (21:00Z the day before). Legacy `between '<from> 00:00Z' and '<to> 00:00Z'` misses it. All 4,460 such rows are July 2026, so a one-day legacy filter on 27 July finds 450 of 1,095. | 5,335 |
| `+reversed` | **Reversal.** Legacy hides reversed payments (`is_deleted`). v2 RPT-06 lists them as `reversed`, counted in `reversed_amount`, never in approved. RPT-07/08 exclude them in both. | 58 |
| `−recorder scope` | **Scope correction (RPT-06/08 only).** Legacy `/payments` for a supervisor matches the *recorder*: `user_id IN (supervised users + self) OR contract IN (supervised contracts)`. The case is **not in the actor's current case scope**, typically because it was reassigned. v2 excludes it. RPT-07 follows the recorder like legacy `/payments/user`, so it has no scope difference. | 244 |
| `+case scope` | **Scope correction.** The case *is* in the actor's current case scope (the owner is the supervisor or a supervised user), but the recorder is someone else and the contract is not supervised, so legacy omits it. v2 includes it. | 3,540 |

Actors:

- **aidi.dev**: ADMIN, supervisor of all 15 contracts.
- **maher.j**: project manager, 12 contracts.
- **bader.b**: مشرف2, 3 contracts, owns 149 cases.
- **marwan.a**: مشرف2, 1 contract and 1 supervised user, owns 4,642 cases.
- **qasim.s**: مشرف2, 0 contracts and 1 supervised user, owns 916 cases.
- **rasha.s, ghofran.b**: collectors with the most recorded payments.
- **tabby.1**: role معالجة, 3,581 recorded payments.

Legacy `totalAmount` is a float sum, e.g. `49268658.93000006`. v2 totals are exact numeric strings. The exact legacy Σ below was computed from the legacy row set.

**Window 2025-01-01 → 2025-12-31**

| Report | Legacy endpoint | Actor | Legacy rows | Legacy Σ (exact) | Legacy `totalAmount` | v2 rows | v2 recorded | v2 approved | Differences (rows, SAR) |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| RPT-06 | `/payments` | aidi.dev | 8,496 | 50,620,259.51 | – | 8,496 | 50,620,259.51 | 49,268,658.93 | none |
| RPT-06 | `/payments` | maher.j | 8,496 | 50,620,259.51 | – | 8,496 | 50,620,259.51 | 49,268,658.93 | none |
| RPT-06 | `/payments` | bader.b | 1,217 | 12,260,097.27 | – | 1,217 | 12,260,097.27 | 11,943,092.88 | none |
| RPT-06 | `/payments` | marwan.a | 4,980 | 10,654,164.10 | – | 5,026 | 11,030,758.27 | 10,922,678.85 | −recorder scope 9 (26,232.88); +case scope 55 (402,827.05) |
| RPT-06 | `/payments` | qasim.s | 81 | 1,278,887.16 | – | 74 | 1,235,700.43 | 1,203,959.75 | −recorder scope 31 (223,704.48); +case scope 24 (180,517.75) |
| RPT-08 | `/payments/approval_status` | aidi.dev | 8,307 | 49,268,658.93 | 49268658.93000006 | 8,307 | 49,268,658.93 | 49,268,658.93 | none |
| RPT-08 | `/payments/approval_status` | maher.j | 8,307 | 49,268,658.93 | 49268658.93000006 | 8,307 | 49,268,658.93 | 49,268,658.93 | none |
| RPT-08 | `/payments/approval_status` | bader.b | 1,192 | 11,943,092.88 | 11943092.879999995 | 1,192 | 11,943,092.88 | 11,943,092.88 | none |
| RPT-08 | `/payments/approval_status` | marwan.a | 4,919 | 10,546,574.98 | 10546574.980000004 | 4,963 | 10,922,678.85 | 10,922,678.85 | −recorder scope 9 (26,232.88); +case scope 53 (402,336.75) |
| RPT-08 | `/payments/approval_status` | qasim.s | 75 | 1,236,113.28 | 1236113.2799999998 | 68 | 1,203,959.75 | 1,203,959.75 | −recorder scope 29 (207,649.28); +case scope 22 (175,495.75) |
| RPT-07 | `/payments/user` | rasha.s | 1,047 | 2,783,670.56 | 2783670.5599999996 | 1,047 | 2,783,670.56 | 2,783,670.56 | none |
| RPT-07 | `/payments/user` | ghofran.b | 880 | 1,951,161.23 | 1951161.23 | 880 | 1,951,161.23 | 1,951,161.23 | none |
| RPT-07 | `/payments/user` | tabby.1 | 0 | 0.00 | 0 | 0 | 0.00 | 0.00 | none |
| RPT-07 | `/payments/user` | marwan.a | 287 | 948,964.87 | 948964.8700000001 | 287 | 948,964.87 | 948,964.87 | none |

**Window 2025-09-01 → 2026-08-31 (a year, including the busy July/August 2026)**

| Report | Legacy endpoint | Actor | Legacy rows | Legacy Σ (exact) | Legacy `totalAmount` | v2 rows | v2 recorded | v2 approved | Differences (rows, SAR) |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| RPT-06 | `/payments` | aidi.dev | 23,090 | 70,764,731.62 | – | 23,090 | 70,764,731.62 | 69,204,880.67 | none |
| RPT-06 | `/payments` | maher.j | 23,089 | 70,762,731.62 | – | 23,089 | 70,762,731.62 | 69,202,880.67 | none |
| RPT-06 | `/payments` | bader.b | 2,175 | 20,426,789.35 | – | 2,175 | 20,426,789.35 | 19,588,679.44 | none |
| RPT-06 | `/payments` | marwan.a | 7,260 | 20,809,293.67 | – | 8,018 | 21,534,745.31 | 21,472,583.70 | +case scope 758 (725,451.64) |
| RPT-06 | `/payments` | qasim.s | 119 | 1,113,295.74 | – | 186 | 1,786,771.58 | 1,712,641.58 | −recorder scope 74 (399,313.35); +case scope 141 (1,072,789.19) |
| RPT-08 | `/payments/approval_status` | aidi.dev | 22,505 | 69,204,880.67 | 69204880.6699994 | 22,505 | 69,204,880.67 | 69,204,880.67 | none |
| RPT-08 | `/payments/approval_status` | maher.j | 22,504 | 69,202,880.67 | 69202880.66999939 | 22,504 | 69,202,880.67 | 69,202,880.67 | none |
| RPT-08 | `/payments/approval_status` | bader.b | 2,127 | 19,588,679.44 | 19588679.439999986 | 2,127 | 19,588,679.44 | 19,588,679.44 | none |
| RPT-08 | `/payments/approval_status` | marwan.a | 7,244 | 20,790,343.38 | 20790343.380000006 | 7,896 | 21,472,583.70 | 21,472,583.70 | +case scope 652 (682,240.32) |
| RPT-08 | `/payments/approval_status` | qasim.s | 115 | 1,077,121.54 | 1077121.54 | 179 | 1,712,641.58 | 1,712,641.58 | −recorder scope 72 (383,258.15); +case scope 136 (1,018,778.19) |
| RPT-07 | `/payments/user` | rasha.s | 1,067 | 3,857,667.80 | 3857667.8 | 1,067 | 3,857,667.80 | 3,857,667.80 | none |
| RPT-07 | `/payments/user` | ghofran.b | 1,230 | 3,008,661.86 | 3008661.8600000003 | 1,230 | 3,008,661.86 | 3,008,661.86 | none |
| RPT-07 | `/payments/user` | tabby.1 | 3,581 | 1,333,520.02 | 1333520.0200000005 | 3,581 | 1,333,520.02 | 1,333,520.02 | none |
| RPT-07 | `/payments/user` | marwan.a | 0 | 0.00 | 0 | 0 | 0.00 | 0.00 | none |

**Window 2026-07-01 → 2026-07-31 (the 4,460 rows stored at Riyadh midnight)**

| Report | Legacy endpoint | Actor | Legacy rows | Legacy Σ (exact) | Legacy `totalAmount` | v2 rows | v2 recorded | v2 approved | Differences (rows, SAR) |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| RPT-06 | `/payments` | aidi.dev | 7,447 | 7,844,381.10 | – | 7,907 | 8,035,400.09 | 7,889,637.77 | +first-day 460 (191,018.99) |
| RPT-06 | `/payments` | maher.j | 7,447 | 7,844,381.10 | – | 7,907 | 8,035,400.09 | 7,889,637.77 | +first-day 460 (191,018.99) |
| RPT-06 | `/payments` | bader.b | 185 | 1,386,768.93 | – | 185 | 1,386,768.93 | 1,300,877.49 | none |
| RPT-06 | `/payments` | marwan.a | 712 | 2,339,605.35 | – | 731 | 2,346,866.09 | 2,337,572.94 | +case scope 19 (7,260.74) |
| RPT-06 | `/payments` | qasim.s | 11 | 59,877.58 | – | 21 | 138,821.63 | 99,917.63 | −recorder scope 9 (38,611.55); +case scope 19 (117,555.60) |
| RPT-08 | `/payments/approval_status` | aidi.dev | 7,372 | 7,698,618.78 | 7698618.7800000245 | 7,832 | 7,889,637.77 | 7,889,637.77 | +first-day 460 (191,018.99) |
| RPT-08 | `/payments/approval_status` | maher.j | 7,372 | 7,698,618.78 | 7698618.7800000245 | 7,832 | 7,889,637.77 | 7,889,637.77 | +first-day 460 (191,018.99) |
| RPT-08 | `/payments/approval_status` | bader.b | 180 | 1,300,877.49 | 1300877.4900000005 | 180 | 1,300,877.49 | 1,300,877.49 | none |
| RPT-08 | `/payments/approval_status` | marwan.a | 707 | 2,333,435.67 | 2333435.67 | 721 | 2,337,572.94 | 2,337,572.94 | +case scope 14 (4,137.27) |
| RPT-08 | `/payments/approval_status` | qasim.s | 11 | 59,877.58 | 59877.579999999994 | 20 | 99,917.63 | 99,917.63 | −recorder scope 9 (38,611.55); +case scope 18 (78,651.60) |
| RPT-07 | `/payments/user` | rasha.s | 64 | 579,175.16 | 579175.16 | 64 | 579,175.16 | 579,175.16 | none |
| RPT-07 | `/payments/user` | ghofran.b | 61 | 83,159.49 | 83159.48999999999 | 61 | 83,159.49 | 83,159.49 | none |
| RPT-07 | `/payments/user` | tabby.1 | 3,121 | 1,142,501.03 | 1142501.0299999993 | 3,581 | 1,333,520.02 | 1,333,520.02 | +first-day 460 (191,018.99) |
| RPT-07 | `/payments/user` | marwan.a | 0 | 0.00 | 0 | 0 | 0.00 | 0.00 | none |

**One day, 2026-07-27** (645 rows stored at 21:00Z on 26 July)

| Report | Legacy endpoint | Actor | Legacy rows | Legacy Σ (exact) | Legacy `totalAmount` | v2 rows | v2 recorded | v2 approved | Differences (rows, SAR) |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| RPT-06 | `/payments` | aidi.dev | 450 | 308,370.24 | – | 1,095 | 517,906.86 | 515,720.15 | +first-day 645 (209,536.62) |
| RPT-06 | `/payments` | maher.j | 450 | 308,370.24 | – | 1,095 | 517,906.86 | 515,720.15 | +first-day 645 (209,536.62) |
| RPT-06 | `/payments` | bader.b | 6 | 66,814.77 | – | 6 | 66,814.77 | 66,814.77 | none |
| RPT-06 | `/payments` | marwan.a | 42 | 57,313.36 | – | 45 | 58,928.39 | 57,489.27 | +case scope 3 (1,615.03) |
| RPT-06 | `/payments` | qasim.s | 1 | 2,600.00 | – | 0 | 0.00 | 0.00 | −recorder scope 1 (2,600.00) |
| RPT-08 | `/payments/approval_status` | aidi.dev | 445 | 306,183.53 | 306183.5299999999 | 1,090 | 515,720.15 | 515,720.15 | +first-day 645 (209,536.62) |
| RPT-08 | `/payments/approval_status` | maher.j | 445 | 306,183.53 | 306183.5299999999 | 1,090 | 515,720.15 | 515,720.15 | +first-day 645 (209,536.62) |
| RPT-08 | `/payments/approval_status` | bader.b | 6 | 66,814.77 | 66814.76999999999 | 6 | 66,814.77 | 66,814.77 | none |
| RPT-08 | `/payments/approval_status` | marwan.a | 42 | 57,313.36 | 57313.36 | 44 | 57,489.27 | 57,489.27 | +case scope 2 (175.91) |
| RPT-08 | `/payments/approval_status` | qasim.s | 1 | 2,600.00 | 2600.0 | 0 | 0.00 | 0.00 | −recorder scope 1 (2,600.00) |
| RPT-07 | `/payments/user` | rasha.s | 4 | 7,467.36 | 7467.360000000001 | 4 | 7,467.36 | 7,467.36 | none |
| RPT-07 | `/payments/user` | ghofran.b | 4 | 8,753.00 | 8753.0 | 4 | 8,753.00 | 8,753.00 | none |
| RPT-07 | `/payments/user` | tabby.1 | 0 | 0.00 | 0 | 455 | 159,331.60 | 159,331.60 | +first-day 455 (159,331.60) |
| RPT-07 | `/payments/user` | marwan.a | 0 | 0.00 | 0 | 0 | 0.00 | 0.00 | none |

**Window 2026-09-01 → 2026-09-30 (the 29 reversed payments)**

| Report | Legacy endpoint | Actor | Legacy rows | Legacy Σ (exact) | Legacy `totalAmount` | v2 rows | v2 recorded | v2 approved | Differences (rows, SAR) |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| RPT-06 | `/payments` | aidi.dev | 1,783 | 3,817,139.55 | – | 1,812 | 3,821,119.55 | 3,747,757.41 | +reversed 29 (3,980.00) |
| RPT-06 | `/payments` | maher.j | 1,783 | 3,817,139.55 | – | 1,812 | 3,821,119.55 | 3,747,757.41 | +reversed 29 (3,980.00) |
| RPT-06 | `/payments` | bader.b | 43 | 357,754.16 | – | 43 | 357,754.16 | 349,754.16 | none |
| RPT-06 | `/payments` | marwan.a | 395 | 1,710,960.26 | – | 616 | 1,756,969.27 | 1,748,472.37 | +case scope 221 (46,009.01) |
| RPT-06 | `/payments` | qasim.s | 1 | 38,346.81 | – | 1 | 38,346.81 | 38,346.81 | none |
| RPT-08 | `/payments/approval_status` | aidi.dev | 1,718 | 3,747,757.41 | 3747757.4099999983 | 1,718 | 3,747,757.41 | 3,747,757.41 | none |
| RPT-08 | `/payments/approval_status` | maher.j | 1,718 | 3,747,757.41 | 3747757.4099999983 | 1,718 | 3,747,757.41 | 3,747,757.41 | none |
| RPT-08 | `/payments/approval_status` | bader.b | 40 | 349,754.16 | 349754.16000000003 | 40 | 349,754.16 | 349,754.16 | none |
| RPT-08 | `/payments/approval_status` | marwan.a | 394 | 1,710,560.26 | 1710560.2600000005 | 590 | 1,748,472.37 | 1,748,472.37 | +case scope 196 (37,912.11) |
| RPT-08 | `/payments/approval_status` | qasim.s | 1 | 38,346.81 | 38346.81 | 1 | 38,346.81 | 38,346.81 | none |
| RPT-07 | `/payments/user` | rasha.s | 53 | 203,078.20 | 203078.2 | 53 | 203,078.20 | 203,078.20 | none |
| RPT-07 | `/payments/user` | ghofran.b | 25 | 85,354.65 | 85354.65 | 25 | 85,354.65 | 85,354.65 | none |
| RPT-07 | `/payments/user` | tabby.1 | 0 | 0.00 | 0 | 0 | 0.00 | 0.00 | none |
| RPT-07 | `/payments/user` | marwan.a | 0 | 0.00 | 0 | 0 | 0.00 | 0.00 | none |

**Window 2026-08-01 → 2026-08-31 (a month)**

| Report | Legacy endpoint | Actor | Legacy rows | Legacy Σ (exact) | Legacy `totalAmount` | v2 rows | v2 recorded | v2 approved | Differences (rows, SAR) |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| RPT-06 | `/payments` | aidi.dev | 5,282 | 11,603,692.07 | – | 5,282 | 11,603,692.07 | 11,485,073.06 | none |
| RPT-06 | `/payments` | maher.j | 5,282 | 11,603,692.07 | – | 5,282 | 11,603,692.07 | 11,485,073.06 | none |
| RPT-06 | `/payments` | bader.b | 182 | 1,057,647.80 | – | 182 | 1,057,647.80 | 1,034,083.85 | none |
| RPT-06 | `/payments` | marwan.a | 830 | 3,403,126.92 | – | 1,435 | 3,517,166.58 | 3,509,617.96 | +case scope 605 (114,039.66) |
| RPT-06 | `/payments` | qasim.s | 0 | 0.00 | – | 13 | 37,481.00 | 37,481.00 | +case scope 13 (37,481.00) |
| RPT-08 | `/payments/approval_status` | aidi.dev | 5,205 | 11,485,073.06 | 11485073.059999978 | 5,205 | 11,485,073.06 | 11,485,073.06 | none |
| RPT-08 | `/payments/approval_status` | maher.j | 5,205 | 11,485,073.06 | 11485073.059999978 | 5,205 | 11,485,073.06 | 11,485,073.06 | none |
| RPT-08 | `/payments/approval_status` | bader.b | 179 | 1,034,083.85 | 1034083.85 | 179 | 1,034,083.85 | 1,034,083.85 | none |
| RPT-08 | `/payments/approval_status` | marwan.a | 830 | 3,403,126.92 | 3403126.9200000013 | 1,406 | 3,509,617.96 | 3,509,617.96 | +case scope 576 (106,491.04) |
| RPT-08 | `/payments/approval_status` | qasim.s | 0 | 0.00 | 0 | 13 | 37,481.00 | 37,481.00 | +case scope 13 (37,481.00) |
| RPT-07 | `/payments/user` | rasha.s | 95 | 629,451.93 | 629451.9299999999 | 95 | 629,451.93 | 629,451.93 | none |
| RPT-07 | `/payments/user` | ghofran.b | 115 | 194,700.39 | 194700.39 | 115 | 194,700.39 | 194,700.39 | none |
| RPT-07 | `/payments/user` | tabby.1 | 0 | 0.00 | 0 | 0 | 0.00 | 0.00 | none |
| RPT-07 | `/payments/user` | marwan.a | 0 | 0.00 | 0 | 0 | 0.00 | 0.00 | none |

The full JSON of all 84 runs (legacy count, replica count, exact Σ, float `totalAmount`, v2 totals and categories with amounts) is produced by `python -m core.reports.perf reconcile --json out.json`.

Reading the differences:

- **No money is lost or invented.** Where v2 and legacy see the same scope (aidi.dev, maher.j, bader.b), the rows are identical except for the two intentional corrections: first-day inclusion and listed reversals.
- **The scope differences are the intentional correction, for RPT-06/08 only.** They show up for supervisors whose supervised users and contracts differ from the owners of their cases (marwan.a, qasim.s).
- **RPT-07 equals legacy `/payments/user` row for row and to the halala in every window**, except the Riyadh-midnight first-day rows that legacy misses (tabby.1: +460 in July, +455 on 27 July). After the lead's decision (recorder only, no case scope), the payments collectors recorded on cases reassigned away are included again: for example rasha.s's 283 rows (SAR 1,413,088.93) in the year window, now 1,067 = legacy 1,067.

## 8. Tests

`docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_rep0 api-test`

**Whole suite: 630 passed** in 154 s, with my changes to `worker/main.py` and `core/bulk/retention.py`; the bulk framework and worker tests all pass. **Stream R0: 56 tests**: support smoke 1, framework 28, exports 18, collections 9.

| Area | Tests |
|---|---|
| Registry, definitions | definition validation (duplicate column, bad default sort, bad type); `GET /v2/reports` by screen key (supervisor 3, collector RPT-07 only, accounting RPT-08 only, none 0); definition endpoint (columns, hidden column omitted, money flag, enum, filter params and semantics, selector and `depends_on`, totals, legacy key); 403 with `required_any`; 404; case-insensitive ids |
| Validation | 16 parametrized 422 cases, one per code; all bad parameters listed at once; RPT-07 rejects `creator`/`status`; normalized `filters` and flat `params` round trip |
| Snapshot and money | a row committed **between** the totals and the page is invisible to both, and visible in the next preview; ten 0.1 payments sum to exactly `"1.00"` |
| Stable paging | **110 fully tied rows × 6 sorts**: 6 pages of 20 = one page of 200, no loss or duplicate, ties ordered by id; an empty page past the end still carries the full totals |
| Selectors | **53 creators**: 50 + 3 across two pages; `selected` from page 2 resolved on page 1; literal `%` and `_`; Arabic search; login searched; unknown field 404; RPT-07 creator 404; `report` required; bad `selected` 422 |
| Selector scope | the supervisor never sees contract B or its portfolio; portfolios follow the contract; an out-of-scope `selected` is not resolved; the recorder branch narrows the creators; 403 without the key |
| Protected fields | test report: a viewer without `users_details` gets `mobile` and `target` null except on their own row; SuperAdmin with `users_details` sees them |
| Export API | idempotent 202 (replay header, one row, one audit); creator-private (404 for others, own list only); 409 not ready; body validation (format, params, unknown keys, sort, 403, 400 without key, paging ignored) |
| **Export = preview** | 110 rows: CSV header = columns and **every cell equals the full preview** in order; totals equal; sha256 equal; private 0600 path; audit rows for create, publish and download; progress 100% |
| Fresh snapshot, batches | a row added after the preview appears in the later export; 1,203 rows → batches `[500, 1000, 1203]` |
| Formula safety | `=HYPERLINK`, `+`, `-`, `@`, TAB, CR, DDE: CSV prefixed with `'`, money untouched; XLSX typed strings with exact text (CR included) and no `<f>` in the sheet XML |
| XLSX | typed values equal the preview (status labels, numeric money `#,##0.00`, dates); summary rows and totals |
| **Arabic XLSX** | both sheets `rightToLeft`; Arabic sheet names and headers; statuses `معتمدة`/`معكوسة`/`بانتظار الاعتماد`; money numeric; civil date; summary in Arabic (row count, `المحصل المعتمد`, `المعكوس`, filter label) |
| **Permission loss** | revoked after batch 1 of 1,100 → `failed/permission_denied`, no file and no artifact, attempt `failed`, download 409; missing at the start → failed; the download rechecks the key (403) |
| Scope at generation | a case moved out of the supervisor's contract after the request is not exported (RPT-06); the same case stays in the recorder's RPT-07 export |
| **Crash / lease** | a `BaseException` mid-export → still `running` with a live lease (not claimable) → lease expired → `retrying` → second worker `succeeded`: 1,050 distinct rows, attempts `[(1, lease_expired, 500 rows), (2, completed, 1050)]`; max attempts → `failed/max_attempts_exceeded`; stolen lease → `lease_lost`, nothing published; graceful stop → `retrying`, not counted, then completes |
| Failures | `too_many_rows` (env limit); unexpected writer error → `retrying` then `failed/internal_error` |
| **Expiry / retention** | TTL 7 days; past `expires_at`: shown `expired`, download 410, listed under `status=expired`; retention deletes the file, sets `expired`/`purged_at`, writes the audit row; a second run does nothing |
| Worker loop | `Worker.next_work()` claims the export; `run_export` with the real heartbeat thread; nothing left afterwards |
| RPT-06/07/08 dates | 7 stored instants around both window ends (20:59:59Z, 21:00Z, 00:00Z): inclusive civil dates, single day, one-sided ranges, all three reports |
| Statuses and totals | pending, approved (two recorders), rejected, reversed: exact totals for every status; status filter and `all`; RPT-08 approved only; RPT-07 approved and recorded by me, including a payment on another collector's case |
| Reversal | before and after a reversal: status `reversed`, approval fields kept, reversal fields, recorded ≠ approved, gone from RPT-07/08; a legacy-deleted, non-reversed payment is excluded |
| Scope | supervisor contract A vs B; accounting role; SuperAdmin; RPT-07 = recorder = me whatever the owner (a payment on another collector's case is included, a reassignment changes nothing, others' recordings on my cases never appear); creator filters never widen; creator branch = recorder branch; contract filter; screen keys (403s) |
| Filters | literal account (`%_7\`), case-insensitive, Arabic `q`, identity `q`, inclusive amount bounds, zero bound |
| Protected fields | no protected employee column in the three reports; the recorder's mobile, identity and email never appear |
| Legacy vs v2 (test DB) | the real legacy `/payments`, `/payments/approval_status` and `/payments/user` on synthetic rows reproduce each category: first-day, reversed, recorder scope |

## 9. Timings on `cawp-db` (474k cases, 31.4k payments; read-only snapshots; median of 5)

The window is the year 2025-09-01 → 2026-08-31. Preview = page 1 (50 rows) plus the exact totals in one snapshot, measured in-process without HTTP.

| Preview | Rows in result | Median | Min–max |
|---|---:|---:|---:|
| RPT-06 aidi.dev (15 contracts) | 23,090 | **249 ms** | 245–261 |
| RPT-06 bader.b (3 contracts) | 2,175 | 176 ms | 170–178 |
| RPT-06 qasim.s (1 supervised user) | 186 | 14 ms | 14–17 |
| RPT-08 aidi.dev | 22,505 | 234 ms | 230–533 |
| RPT-07 rasha.s (collector) | 1,067 | 15 ms | 15–17 |
| RPT-06 aidi.dev, `q=محمد` | 1,334 | 438 ms | 432–472 |
| RPT-06 aidi.dev, sort `customer_name` | 23,090 | 250 ms | 244–252 |
| RPT-06 aidi.dev, sort `-amount`, page 200 | 23,090 | 256 ms | 248–259 |
| Selector `creator` / `contract` / `portfolio` / `creator_branch` (aidi.dev, RPT-06, all dates) | 177 / 13 / 154 / 2 | ~100 ms each | 99–107 |

Full export of RPT-06 for the year (aidi.dev, 23,090 rows, batches of 500, fresh snapshot, measured without tracemalloc; peak heap measured in a second pass):

| Format | Time | Rows/s | File | Batch median | Python heap peak |
|---|---:|---:|---:|---:|---:|
| CSV | **0.98 s** | 23,599 | 9.4 MB | 10 ms | 2.4 MB |
| XLSX | **1.62 s** | 14,262 | 4.6 MB | 15 ms | 2.3 MB |
| Arabic XLSX | **1.53 s** | 15,127 | 4.6 MB | 15 ms | 2.3 MB |
| (openpyxl write-only, before the switch) | 32 s | 721 | 4.6 MB | 282 ms | 355 MB |

The preview cost for large supervisor scopes comes from the scope predicate:

- `c.user_id = ANY(…) OR c.portfolios_id IN (…)` is planned as a parallel seq scan of `clients` (~75 ms), once for the totals and once for the page.
- The totals themselves (hash join of 23k payments, `count(DISTINCT)`) cost ~60 ms.

This is acceptable for R0. An optimization is listed in §10.

## 10. Open items

1. **Integration (lead):**
   - apply `0039`–`0040` with `manage_db.py up`: new tables, instant;
   - restart the API and the worker;
   - add rows for 0039/0040 to `db/README.md` (not my file);
   - the `docker-compose.yml` worker comment still says "Reviewed bulk jobs" only.

   **0041 is unused**. No index was needed: the payment reports scan 31k rows. It can be released to another stream or kept in reserve.
2. **RPT-07 semantics: decided** (lead, 2026-09-24): recorder = me, approved, no case-scope intersection (§2, §7). Stream R-A should apply the same rule to RPT-05.
3. **`lxml`:** openpyxl's write-only mode is O(rows) in memory without it. The exports use `core/reports/xlsx.py` instead. If `lxml` is added to `requirements.txt` later, openpyxl could be used again, but the streaming writer is already faster.
4. **Download reauthorization:** the download rechecks the report key and that the requester is the creator (active account through the JWT/session checks). It does **not** re-apply the current case scope to the rows inside an already published file. The rebuild reauthorized every recorded case. The file is the requester's own snapshot and expires in 7 days; per-case reauthorization would need a provenance table of exported case ids (possible later).
5. **Preview performance for wide scopes:** the supervisor scope makes the planner seq-scan `clients` twice per preview (~150 ms of the 250 ms). Possible fixes, if needed for larger data:
   - a MATERIALIZED CTE of the scoped payment ids;
   - resolving the supervisor's portfolios to an array first, as `core/bulk/common.scope_sql` does;
   - caching the totals per (definition, snapshot).
6. **Scope cache:** the preview and selectors use `resolve_scope` with its 60 s cache, like every other list. The worker drops the cache (fresh scope).
7. **Legacy findings, unchanged and carried forward:**
   - `/payments*` are JWT-only; the keys are only on the screens;
   - `totalAmount` is a float;
   - the date `between` misses Riyadh-midnight rows, which a one-day filter on 27 July shows most clearly (450 of 1,095);
   - the legacy Excel status column shows pending payments as "مرفوض".
8. **Generic legacy operators** (MUI filter, `*_Qkey`) are not mapped; v2 has explicit filters only. The rebuild left these open too.
9. **Frontend** (after the backend): the reports hub, the generic viewer driven by `GET /v2/reports/<id>`, export progress and download. The contract is in §4.
