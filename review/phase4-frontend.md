# Phase 4 — frontend (reports hub and generic report viewer)

Date: 2026-09-24. Branch `phase-4-reports`. Nothing is committed. Scope: `CAWP-frontend/**` only. No API, DB, Docker or docs changes, and no container restarts.

The contract comes from `review/phase4-framework.md` and `review/phase4-reports-{a,b,c}.md`. I checked it against the live API on :9797 (worker and the CDR fixture `cawp-cdr-test-db` running) and against `CAWP-API/api/reports_v2.py`, `core/reports/*` and `services/reports_v2_{a,b,c,collections}.py`. Where the reports and the live API differ, I followed the live API (§6).

## 1. Screens, routes and keys

| Screen | Route | Guard key | Nav (Recovery & team) | Main file |
|---|---|---|---|---|
| Reports hub (reports grouped by family, search, classic links, "My exports" tab) | `/reports` | `reports_any` (derived) | "Reports hub", before the legacy "Reports" sub-section | `src/views/phase4/hub/index.tsx` |
| Generic report viewer | `/reports/:id` | `reports_any` (derived); every call re-checks the report's own key | — | `src/views/phase4/viewer/index.tsx` |

- **`reports_any` is derived**, like Phase 2's `bulk_jobs` and Phase 3's `team_any`. It is true when the role holds any report key (`REPORT_KEYS` in `src/utils/v2/derivedPermissions.ts`: the 21 any-of keys of RPT-01…25 on the live registry). That mirrors `GET /v2/reports` being non-empty. The hub then shows exactly what the API returns. A report the user may not open answers 403 on its definition, and the viewer shows the unauthorized state with `required_any`.
- The script checks that `REPORT_KEYS` covers every key the live definitions use.
- Nav icon: Lucide `FileChartColumn`. The legacy report screens and their nav entries are unchanged. They stay as "classic" entries.

### Hub
- The cards are `GET /v2/reports`, grouped by family in the API's `families` order. Each card has:
  - the id;
  - the label in the current language, with the other language underneath;
  - the description;
  - the formats the definition offers (CSV / XLSX / XLSX AR);
  - "Open report";
  - the report's classic legacy screen(s), when the user holds that route's key.
- Search is literal and case-insensitive over the id, both labels and both descriptions (e.g. Arabic in the English UI). It lives in the hash (`q`, replace history), and a no-match state offers to clear it.
- "Classic report screens" lists every legacy report screen the user can open (map in `reportUtils.CLASSIC_LINKS`, from the definitions' `legacy` field and `routes.config.ts`). RPT-11, RPT-24 and RPT-25 have no standalone legacy screen.
- The "My exports" tab has a status filter and paging in the hash (`tab`, `xstatus`, `xpage`).

### Viewer (everything from `GET /v2/reports/<id>`)
- **Header:**
  - the id, the label and the description;
  - the definition's fixed rules (`fixed`) as chips;
  - the classic link;
  - Refresh.
- **Filters** (`FilterPanel.tsx`), by the filter's `type`:

  | Type | Control | Sent as |
  |---|---|---|
  | `date_range` | two date inputs; hint "Inclusive dates (Riyadh)" | `<key>_from`, `<key>_to` |
  | `timestamp_range` | two date inputs; hint "Whole days, Riyadh time" | same |
  | `enum` multi | filter chips | comma list, in choice order |
  | `enum` single | segmented control ("All" unless the API has a default); radio chips when there are more than 3 choices (RPT-24 section) | one value |
  | `ids` | scoped async selector (`IdsFilter.tsx`) | comma list of UUIDs |
  | `text` | input, applied on Enter or blur | literal |
  | `amount_range` | two inputs, applied on Enter or blur | exact SAR strings |
  | `month` (RPT-19), `date` (RPT-22 `as_of`, RPT-23 `day`), `hour_range` (RPT-23) | month / date inputs, two hour selects | as the API names them |
  | unknown type | a text input per param (a future report still opens) | raw |

  - Required filters carry a red `*` (a legend is in the panel header). Each filter shows its semantics, `max_days` and the server default as a hint.
  - "N changed" counts the filters that differ from their defaults, and "Reset to defaults" restores them.
- **Ids selectors** (`GET /v2/report-options/<field>?report=`):
  - pages of 50 with "Load more";
  - a debounced literal search `q`;
  - the label and the secondary line (e.g. the contract of a portfolio), with an "inactive" mark;
  - chips for the chosen values;
  - `selected=<ids>` resolves labels beyond page one after a reload or a shared link. An id the API does not return stays in the filter, marked "Not in your list".
  - **`depends_on`**: the selector sends the current params of the filters it depends on (e.g. `contract` for portfolios, `created_*` for every RPT-04/05 selector) and the params of every required filter, because the options endpoint validates required filters (R-A §8.3). Changing an ids filter clears the ids filters that depend on it (portfolio follows contract). A date change does not clear anything.
- **Defaults** (`reportUtils.clientDefaults`):
  - **RPT-16:** the current Riyadh month (first to last day) on `callback`, as the R-B notes ask; the API has no default there.
  - **RPT-04/05:** `created` is required, so it defaults to the current Riyadh month (R-A §8.6).
  - **Server defaults are mirrored**, so the filter shows what the API applies and the request states it: RPT-18/20/21/25 `today`, RPT-24 month to date, RPT-19 the current month, RPT-22/23 today, RPT-23 hours 8–17 and mode `notes`, RPT-24 section `summary`.
  - Clearing a default on purpose (e.g. no callback window on RPT-16) writes `~` in the hash, so it survives a reload.
- **422:** `validation_failed` `{param: code}` is shown under each filter in the current language (`p4.fieldErr.*`: invalid, required, reversed_range, range_too_long with the limit, too_long, too_many, unknown_value, negative, too_precise, too_large, unknown_parameter). A summary line names each filter ("Payment amount (From): At most 2 decimals"). Every bad parameter shows at once. While a filter is invalid no stale rows are shown.
- **Totals strip:** every total of the definition, over the full filtered result (same snapshot as the page).
  - Money is the API's decimal string, re-grouped as text only, with "(SAR)" in the label. Counts are grouped.
  - A `nullable` total that comes back null is **"Unknown"**, never 0.
- **Table** (`ReportTable.tsx`, cells in `cells.tsx`):
  - **Cells by column type:**
    - money: exact string, trailing-aligned, currency in the header;
    - `datetime`: Asia/Riyadh `YYYY-MM-DD HH:mm`, full ISO in the tooltip;
    - `date`: as returned;
    - `int`: grouped;
    - `bool`: Yes/No;
    - `enum`: the definition's label in the current language, as a status pill (glyph + tone) for status/state/follow-up/PBX/attribution columns;
    - `case_id`: a link to the case;
    - other UUIDs: short, with the full id in the tooltip;
    - text: `dir="auto"`, truncated with a tooltip.
  - **Protected employee fields** (the column's `protected` flag): a null is shown as **"Hidden"** (lock pill, tooltip "Protected employee field: hidden for your permissions") when the viewer lacks the right (`users_details`/`users_edit`; for targets also `users_edit_target`, mirroring `core/user_privacy.py`). Otherwise it shows "—". Protected column headers carry a lock icon.
  - **Sort:**
    - toggles on the headers that have a sort (first click descending for money, number and date columns, ascending for text; then it flips);
    - a "Sort by" select lists every sort of the definition, including those without a column;
    - the id tie-breaker is the API's, so paging is stable.
  - **Paging:** page size 25/50/100/200 (max 200, as the API), the pager, "Rows 1–50 of N", the total row count and "Data as of" (the snapshot time, Riyadh).
  - **Sectioned reports** (a `section` enum column: RPT-18, RPT-24) render one group row per section. RPT-24's summary row is always first and bold, and the totals strip is the summary.
  - For RPT-18/23/24, columns empty on every row of the page are collapsed by default ("Show N empty columns"), e.g. RPT-23's hours outside the window and the other mode's counters.
  - **RPT-25 recordings:** the `recording_url` column renders download / play buttons **only** for holders of `clints_callsRecords_download` / `clints_callsRecords_play` (download needs the first, play the second). They call the existing `GET /cdr/recording?recordingPath=` through `apiGetCdrRecordingPath`, like the case call-records tab; there is no new route.
    - Without a key the API returns null links, and a recorded call shows "No access" (the tooltip explains which permission is needed).
    - `has_recording` stays visible.
- **Export** (`exports/ExportPanel.tsx`):
  - one button per format in the definition: CSV, XLSX and "Arabic XLSX (RTL)" only when `formats` has `xlsx_ar`. Otherwise a line says why ("its legacy screen did not export one").
  - The note **"Exports use a fresh snapshot; they may differ from the preview if data changed."** is always shown.
  - `POST /v2/reports/<id>/exports` sends `{format, params, sort}`, exactly the preview's non-empty params and sort, with one Idempotency-Key per action. A retry of the same request after a failure reuses the key.
  - The job is polled every 1.5 s while queued/running/retrying. It shows the status pill, the progress bar and "rows written of expected".
  - When the job succeeds, the panel shows:
    - the row count, compared with the preview ("same as the preview", or a warning with both numbers);
    - the file size, expiry and cutoff (Riyadh);
    - **Download** (blob with the bearer token, saved under `artifact.file_name`).
  - A failed job shows its code in words (`p4.exportError.*`).
  - Changing the filters clears the card; the job itself goes on and stays in "My exports".
- **My exports** (`exports/MyExports.tsx`), below the viewer (this report only) and as the hub tab (all reports):
  - requested at, report (a link that reopens the report with the export's params and sort), format, status (with progress or the failure reason), rows, file name and size, expiry (Riyadh, or "Expired") and Download;
  - a 2-second refresh while any export is active;
  - paging.
- **Charts** (`viewer/TrendChart.tsx`), only for the time series:
  - **Reports and measures:**
    - RPT-20: approved amount or receipts per day;
    - RPT-21: promised amount or promises per day;
    - RPT-23: total per Riyadh hour of the window;
    - RPT-24: the daily trend section, approved amount, notes or approved payments.
  - **Following `design-reference/dataviz.md`:**
    - bars from zero, horizontal grid only, one measure per axis (a segmented switch);
    - the time axis never mirrors (oldest on the left in Arabic too);
    - abbreviated axis ticks (1.5M) with exact values in the tooltip, in the sub-line total and in a "Show exact values" list;
    - orange only for the hovered bar;
    - a gap for an unknown value.
    - RPT-20/21 days without rows are true zeros (no receipts/promises that day), and the sub-line says so.
    - A skeleton while loading.
  - **Correctness guard:**
    - The chart reads every row of the filter (pages of 200, up to 800 rows; beyond that it says to use the table).
    - It sums money exactly in halalas with `BigInt`.
    - The chart is drawn only if every page carried the same totals **and** its sum equals the report's own exact total for that measure. Otherwise it says the data changed while loading and offers a reload.
  - On RPT-24 outside the trend section, a button switches to the trend.
- **Hash state:** every filter param under its API name, plus `sort`, `page` and `size`. The report id is the path. Reload, Back/Forward and shared links reproduce the exact request. Defaults are left out of the hash, so the plain report has an empty hash.

## 2. Files

**New:**
- `src/services/v2/p4Types.ts` (wire types), `p4Api.ts` (`reportsApi`: list, definition, rows, options, createExport, exports, exportDetail; `downloadReportExport`), `p4Errors.ts` (`describeReportError`, `reportFieldErrors`, `fieldErrorText`, `exportFailureText`)
- `src/views/phase4/reportUtils.ts`: labels, Riyadh month, client defaults, hash schema/codec, params, sort mapping, protected-field rights, exact cents, classic links, chart specs
- `src/views/phase4/hub/index.tsx`
- `src/views/phase4/viewer/{index,FilterPanel,IdsFilter,ReportTable,cells,TotalsStrip,TrendChart}.tsx`
- `src/views/phase4/exports/{ExportPanel,MyExports,exportParts}.tsx`, `exportUtils.ts`
- `scripts/verify-phase4.cjs`

**Changed (additive):**
- `src/services/v2/index.ts`: exports
- `src/utils/v2/derivedPermissions.ts`: `REPORT_KEYS`, `reports_any`
- `src/configs/routes.config/routes.config.ts`: 2 routes
- `src/configs/navigation.config/index.ts`: 1 nav item
- `src/configs/navigation-icon.config.tsx`: 1 icon
- `src/locales/lang/{ar,en}.json`: **192 keys under `p4.*` plus `nav.p4.reports`**, identical key sets. Insertions only; the files keep their format (a JSON round trip reproduces them byte for byte).
- `scripts/verify-phase0.cjs`, `scripts/verify-phase3-flows.cjs`: expectation changes (§4)

## 3. Build, type check, lint

| Check | Before | After |
|---|---|---|
| `npm run build` | passes | **passes** (only the usual chunk-size warning) |
| `tsc --noEmit` | 4654 | **4654**. No file gained an error; the new files have 0. |
| `eslint . --ext .ts,.tsx` | 823 errors / 2487 warnings | **823 / 2487**. The new files: 0 errors, 0 warnings. |
| Exact-case imports (resolver over every new and changed source file, 242 imports) | — | 0 problems |
| Linux case-sensitive build (sources `tar`-copied into a throwaway `node:22-alpine`, `npm ci && npm run build`) | — | **passes**, on a **case-sensitive APFS volume** (sources copied onto a `Case-sensitive APFS` disk image; `vite build` exit 0). A negative control (`./cells` imported as `./Cells`) fails there with `Could not resolve "./Cells"`, so the check is real. The Phase 0 container method (`tar` into a throwaway `node:22-alpine`, `npm ci && npm run build`) ran `npm ci` fine, but the build was OOM-killed (exit 137, three tries). The Docker VM has 3.8 GiB, and about 1.7 GiB is held by the running stack, now including `cawp-cdr-test-db`. I did not stop any container. |

## 4. Verification

| Script | Result |
|---|---|
| **`verify-phase4.cjs`** (new) | **186/186**. Log `logs/screens/phase4/verify.log`, `verify.json`, 31 screenshots (ar and en) |
| `verify-phase0.cjs` | **47/47**, after 2 expectation changes (below) |
| `verify-phase1.cjs` | **58/58. A first run, made while the Linux container build was starving the machine, failed 3 checks on timeouts (the reversal landed after the check read the balance). A clean rerun passed.** |
| `verify-phase1-reviewer.cjs` | **9/9** |
| `verify-phase2.cjs` | **119/119** |
| `verify-phase3.cjs` | **124/124. A first run had 1 failure: the plans wizard's refusal text was correct but in Arabic, where the check reads the English sentence (a language-toggle flake in an unchanged screen). `ONLY=plans` passed 22/22, and a full rerun passed 124/124.**, after 1 expectation change (below) |

**Expectation changes**, all caused by the new nav item:
1. `verify-phase0.cjs` and `verify-phase3-flows.cjs`: the aidi.dev sidebar count per group goes from `[6,12,9]` to **`[6,13,9]`** (Reports hub in Recovery & team).
2. `verify-phase0.cjs`: the stubbed "admin-only role" (`users`, `roles`) now also shows **Recovery & team**. `users` is RPT-01's key (users roster, screen-key rule), so `reports_any` is true and the hub appears. The expected groups are now Recovery & team + Intelligence & control.

**What `verify-phase4.cjs` covers** (sections runnable with `ONLY=`):
- **hub:**
  - for aidi.dev (23), aidi.collector (9) and aidi.reviewer (12), the cards are exactly `GET /v2/reports`, grouped in the API's family order, and the tab count equals `total`;
  - aidi.collector sees exactly RPT-02/05/07/10/11/16/17/18/19, and `/reports/RPT-06` shows the unauthorized state;
  - the labels in both languages and the description;
  - search by word, by id and in Arabic; search in the hash, surviving a reload; the no-match state;
  - classic links (one opens the legacy neglect screen, which still works);
  - sidebar `[6,13,9]`; a card opens the viewer;
  - `REPORT_KEYS` covers every key the API uses.
- **open:** every report the caller can see (aidi.dev: 23; aidi.collector: 9) opens with its default filters:
  - rows 200;
  - the table rows equal the response items;
  - the row count and the count tile equal the API;
  - no error state and no page error.
  - Default checks:
    - RPT-16: current Riyadh month;
    - RPT-04/05: required `created` = current month, marked required;
    - RPT-25: today;
    - RPT-19: current month;
    - RPT-23: hours 8–17, mode notes;
    - RPT-24: month to date with the summary section first;
    - RPT-18: today, grouped rows;
    - RPT-20/21: today.
- **filters:** on RPT-06 each action changes the request exactly as expected:
  - the first request is only sort/page/page_size;
  - status chips give `approved,reversed`;
  - payment-date from/to;
  - the contract selector calls `report-options/contract?report=RPT-06`, its options equal the API page, typing sends `q`, and picking sends `contract=<id>`;
  - the portfolio selector sends its `depends_on` contract;
  - amount on Enter;
  - header sort `-amount` then `amount`;
  - everything is in the hash.
  - **Reload** sends exactly the same request, resolves the contract label with `selected=`, and restores the header sort and the inputs.
  - Page 2, Back to page 1, Back again undoes the sort, Forward re-applies it.
  - **422:**
    - `amount_from=10.555` gives `too_precise` under the field in English and Arabic, plus the summary line;
    - a reversed date range together with it shows both codes at once;
    - reset returns to the default request.
  - **RPT-16:** a cleared window sends no callback params and survives a reload (`~`).
  - **RPT-04:** the recorder selector sends `created_from/to`; single-choice `note_selection`.
  - **RPT-23:** hour and mode.
- **totals:**
  - RPT-06 (July), RPT-12, RPT-02, RPT-09 (September) and RPT-25 (fixture day): **every total tile's raw value equals the API string, and its text equals the exact re-grouping**;
  - RPT-06 money cells are the API string, and `created_at` is shown in Riyadh time;
  - "Unknown" for a nullable total returned as null (stubbed: no live total is nullable yet, §6.1).
- **export:**
  - the snapshot note; RPT-06 offers the three formats;
  - the POST body is exactly the preview params and sort, with an Idempotency-Key;
  - queued, then polled to succeeded.
  - **CSV** (RPT-06 `status=reversed`), downloaded through the UI and parsed:
    - server file name;
    - UTF-8 BOM;
    - header = the export columns;
    - **data rows = preview total (29)**;
    - every row reversed, amounts exact;
    - the file's amounts sum to the preview's `reversed_amount` (3,980.00) exactly.
  - **Arabic XLSX** (RPT-06, September 2026), downloaded and read with python `zipfile`:
    - `-ar.xlsx` file name;
    - **both sheets right-to-left**, with the Arabic sheet names "تحصيلات المستخدمين" and "الملخص";
    - **data rows = preview total (1,812)**;
    - no formulas.
  - A new filter clears the export card; RPT-24 offers no Arabic XLSX.
- **exports:**
  - "My exports" equals the API page, in order, with the same total;
  - this run's exports are listed as succeeded, each with its expiry in Riyadh time;
  - the status filter sends `status=succeeded` and is kept in the hash;
  - another user does not see them.
- **hidden:**
  - aidi.collector: RPT-19's definition marks the five personal target settings as protected. Another collector's row, stubbed exactly as the API redacts it (§6.2), shows "Hidden" in exactly those five columns, and "—" for an ordinary null. The headers carry the lock. Arabic "مخفي".
  - **Live:** aidi.reviewer's test role temporarily gets `dashbord_admin`. The API nulls every protected setting on all RPT-22 rows, the UI shows "Hidden" in every one (250 = 50 × 5), and the effective target stays visible.
- **recording:**
  - aidi.dev: 8 recorded calls on the fixture day, each with download and play controls pointing at `/cdr/recording`.
  - aidi.reviewer's role temporarily loses both recording keys: the API returns no links, and the UI shows no control but "No access" on the 8 recorded calls.
  - The role is restored exactly (34 keys), also on failure.
- **screens / charts:**
  - hub, viewer, RPT-24 summary, RPT-01 and the collector hub, in ar and en;
  - RPT-18 sections (collector);
  - charts for RPT-20 (July), RPT-21 (September), RPT-24 trend and RPT-23 calls, in both languages. Each is drawn only when its sum equals the report's exact total, and the sub-line total is the exact API string (e.g. 7,889,637.77 for RPT-20 July). The value list is one click away, and the time axis stays left-to-right.

**Screenshots** in `logs/screens/phase4/`:
- `01_hub`, `02_viewer_rpt06`, `03_chart_rpt20`, `03b_chart_rpt21`, `04_chart_rpt24_trend`, `05_chart_rpt23_calls`, `06_rpt24_summary`, `07_rpt18_sections_collector`, `08_rpt01`, `09_hub_collector`, `20_filter_error`, `31_my_exports`, `40_hidden_collector`, each in `_ar` and `_en`;
- `10_viewer_rpt06_en`, `30_export_done_en`, `41_hidden_reviewer_rpt22_en`, `50_rpt25_recordings_en`, `51_rpt25_no_recording_key_en`.

**Test data written:** only report exports by aidi.dev (private, expiring after 7 days): 2 per full run, plus earlier development runs. aidi.reviewer's role was changed for about a minute per run and restored exactly. The recording download is never clicked, because `/cdr/recording` would reach the configured PBX over SSH.

## 5. Decisions

- **One generic viewer for all 25 reports.** There is no per-report screen code. The only report-specific pieces are:
  - the defaults the notes ask for (RPT-04/05/16);
  - the classic-link map;
  - the chart specs;
  - the three reports whose empty columns collapse (RPT-18/23/24).
- **Server defaults are sent explicitly.** The API would apply them anyway; stating them makes the filter show the real window and makes the export body carry the same params as the preview.
- **Text and amount filters apply on Enter or blur**, not per keystroke. Dates, chips and selectors apply at once. Each applied change is one history entry and one request.
- **"Hidden" is decided by the column's `protected` flag and the viewer's keys.** Only a null in a protected column, for a viewer without the right, is "Hidden". A viewer with the right sees "—" for a real null. The API does not mark redacted cells, so a genuinely empty protected field on another employee's row also reads "Hidden" for a viewer without the right.
- **The export card follows the current request.** A new filter clears it; "My exports" keeps every export.
- **Charts are withheld rather than drawn wrong.** They draw only when the pages agree and the sum equals the exact total.

## 6. API findings (exact requests)

1. **No live total is `nullable`.**
   - `Total.nullable` exists in `core/reports/definition.py` and `describe()` returns it, but every total of the 25 definitions has `"nullable": false`.
   - R-C avoided null totals by splitting them into known-only sums plus unknown counts (e.g. RPT-25 `duration_known_seconds` + `duration_unknown_count`).
   - The "Unknown" rendering is therefore verified with a stubbed response. It is ready for when a nullable total is added.
2. **A collector can never see another employee's protected field.** The only report a collector holds that has protected columns is RPT-19, and a collector gets only their own row, which is never redacted.
   - `GET /v2/reports/RPT-19/rows` as aidi.collector returns `{"total":0,…}` (RPT-19 lists active collectors with a contract), so there is no row at all.
   - So "Hidden for a collector" is verified with a stubbed row shaped like the API's redaction, and **live with aidi.reviewer** (a supervisor role without user keys, temporarily given `dashbord_admin`): `GET /v2/reports/RPT-22/rows?page_size=50` returns 50 rows with `user_target_kind/value/revision/from_month/record` all null.
3. **The review notes have drifted on RPT-11.** `review/phase4-reports-b.md` §2/§9 gives RPT-11 the key `reports_performance` only. The live definition has `permissions: ["reports_performance","report_collectors_currentUser_paymentDates"]` (the option the lead chose), so aidi.collector sees RPT-11. The frontend follows the API.
4. **`Content-Disposition` is not exposed cross-origin** (`GET /v2/report-exports/<id>/download` answers without `Access-Control-Expose-Headers`). The file name comes from `artifact.file_name`, which is the same name.
5. **`GET /cdr/recording` is JWT-only** (R-C open item 3, unchanged). The viewer shows recording links only to holders of the recording keys, and the API nulls the links for everyone else. The route itself should still check the keys and the record's visibility.
6. **The selectors validate every filter of the report,** not only `depends_on`: e.g. `GET /v2/report-options/recorder?report=RPT-04` gives 422 `{"created_from":"required"}`. The frontend sends the params of the required filters with every selector call (R-A §8.3).

## 7. Open items

- **Lead decisions carried from the backend:**
  - RPT-11's collector key (§6.3);
  - RPT-01 employee scope;
  - target privacy (RPT-19/22 show the effective target to report holders; only the personal settings are protected).
- **Chart size:** the chart reads up to 800 rows (4 pages). RPT-20/21 over their 92-day maximum can exceed that for a wide scope; the chart then says so and the table has every value.
- **Dates use the browser's date input.** Its display follows the browser locale (e.g. `dd/mm/yyyy`); the value sent is always `YYYY-MM-DD`.
- **Very long id selections:** the hash is capped at 8 KB by `useHashState`, which holds about 200 UUIDs. Longer selections are not kept in the link.
- **RPT-25 is wide** (31 columns) and scrolls horizontally; the recording controls are the last column.
- **Dev server:** still running on :5177 (PID 55329).
