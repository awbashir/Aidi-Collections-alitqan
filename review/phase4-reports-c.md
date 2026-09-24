# Phase 4, stream R-C: RPT-17 … RPT-25 (performance, targets, PBX calls)

Date: 2026-09-24. Branch `phase-4-reports`, not committed. Scope: stream R-C in `docs/phase4-spec.md`.
**Migration 0046 was not applied to `cawp-db`**, and no service container was restarted (the lead does both).
Every measurement on `cawp-db` ran read-only: the connection had `default_transaction_read_only=on` and the
queries ran in the framework's `REPEATABLE READ READ ONLY` snapshot. **The real PBX (10.10.0.220) was never
contacted.** Every CDR read went to the local fixture, and the harness and the tests refuse that address.

## 1. Files

| Area | Files |
|---|---|
| Reports | `CAWP-API/services/reports_v2_c.py`: RPT-17 … RPT-25 and the stream's filter types (`InnerDateRange`, `CallDateRange`, `MonthFilter`, `DayFilter`, `HourRange`, `InnerEnum`, `CallsModeEnum`, `InnerIds`) |
| CDR source | `CAWP-API/services/reports_v2_cdr.py`: MySQL reader (Asterisk CDR and call center), phone key, attribution, SQL relation |
| Migration | `db/migrations/0046_contact_phone_key_index.sql`: `CREATE INDEX CONCURRENTLY` on the contacts' phone key (no-transaction). **0047 is unused.** |
| CDR fixture | `db/cdr-test/init/01_schema.sql`, `02_seed.sql`; `docker-compose.yml`: new service `cdr-test-db` (profile `cdr-test`) |
| Tests | `CAWP-API/tests/test_reports_v2_c_support.py`, `test_reports_v2_c.py`, `test_reports_v2_c_cdr.py`, `tests/fixtures/seed_reports_c.sql` |
| Harness | `CAWP-API/tests/reports_v2_c_parity.py`: `reconcile` and `timings` on `cawp-db` (not collected by pytest, like the other `*_parity.py` scripts) |

Not touched: `core/reports/*`, `app.py`, `issable/db_connection.py` (read-only use), `models.py`, `core/permissions.py`,
`conftest.py`, `seed.sql`, the frontend, and the other streams' files.

## 2. Keys (screen-key rule) and evidence

| Report | Legacy endpoint → screen | Key(s), any-of | Evidence |
|---|---|---|---|
| RPT-17 my all-time summary | `GET /dashbord/user` → the user dashboard (its old `Statistic` card is no longer rendered) | `dashbord_user` | endpoint of the dashboard's "مستخدم" half; `ProjectDashboard.tsx:72,88` gate the user's own dashboard with `dashbord_user` |
| RPT-18 dated collector dashboard | `GET /dashbord/collector` → collector dashboard (`services/DashService.ts:23`) | `dashbord_user` | rendered behind `dashbord_user` (rebuild evidence `DashboardBody.tsx:217-240`) |
| RPT-19 performance vs targets | `GET /dashbord/collector/v2` → `KPICollector` + `DailyPaymentsAll` (`ProjectDashboard.tsx:88-110`); team view `/v2/targets/achievement` | `dashbord_user`, `dashbord_admin` | Phase 3 `team_any` (`navigation.config/index.ts:346`): collectors see their own row, `dashbord_admin` sees the team |
| RPT-20 daily approved recovery | `GET /dashbord/admin/dailyPayments/v2` → `DailyPaymentsAllAdmin` (`ProjectDashboard.tsx:116-130`) | `dashbord_admin` | same |
| RPT-21 daily promised | `GET /dashbord/admin/dailyPaymentDates/v2` → `DailyPromisePaymentsAllAdmin` | `dashbord_admin` | same |
| RPT-22 collector comparison | `GET /dashbord/Allcollector/v2` → `AllCollectorsKPI` | `dashbord_admin` | same; the route is already gated with it |
| RPT-23 hourly notes / calls | `GET /dashbord/commentsReport/v2` → `CommentsReportDaily` + `CallsReportDaily` | `dashbord_admin`; **calls mode also** `clints_callsRecords` or `reports_calls` | `ProjectDashboard.tsx:132-140`. Every role with `dashbord_admin` on `cawp-db` also holds `clints_callsRecords`, so nobody loses the calls table |
| RPT-24 aggregate dashboard | `GET /dashboard` → `views/dashboard` (removed from the frontend) | `reports_performance` | no legacy screen left; today only ADMIN holds the key |
| RPT-25 call detail records | `GET /cdr_by_date`, `GET /cdr` → case detail "سجلات المكالمات" (`ClientDetail.tsx:126,372`, `CallsRecordClientTable.tsx`) | `clints_callsRecords`, `reports_calls` | `reports_calls` lets an admin grant the cross-case report without the case tab |
| RPT-25 recording link | `CallsRecordClientTable.tsx:220` / `:233` | `clints_callsRecords_download` or `clints_callsRecords_play` (**separate** from the metadata) | tested: the recording keys alone give 403 on the report |

Holders on `cawp-db`:
- محصل (119 active) and معالجة: `dashbord_user`, so RPT-17/18/19 (own row);
- مشرف2, مدير مشاريع, عمليات, مدير تحصيل and ADMIN: `dashbord_admin` and `clints_callsRecords`, so RPT-19 … 23 and 25;
- ADMIN only: `reports_performance`/`reports_calls`, so RPT-24.

No report offers the Arabic XLSX. The legacy dashboards exported English-header CSVs (`DailyPaymentsAllAdmin.tsx:87-110`), not Arabic workbooks.

## 3. Shared definitions and decisions

- **Recorder rule (lead decision).** RPT-17 and RPT-18 count the caller's own recorded notes and payments, whoever owns the case now (like RPT-07), with no case-scope predicate. Assignments are the caller's current cases.
  - Tested: reassigning a case moves the assignment count to the new owner, and the recorded work stays with the recorder.
- **Team/global reports** use the current case scope for money and notes.
  - Their employee rows are the caller's **user scope**: SuperAdmin all; supervisor = supervised users + self + users configured on a supervised contract; collector = self.
  - Money and notes come only from in-scope cases.
  - Historical relationships never widen access.
- **Approved recovery** = `approval_status` true, not deleted, and no `payment_reversals` row. Money is exact `numeric`, serialized as strings.
  - Payment and promise dates are inclusive Riyadh civil dates.
  - Note and CDR times use Riyadh day/hour bounds.
  - Effective notes = `"Comments".is_deleted = false` (retired notes excluded).
- **Targets** (RPT-19/22) are the Phase 3 `services/targets.py` semantics, re-done in one SQL statement:
  - in force = the latest month ≤ M, highest revision;
  - for the current month or later, the legacy columns are used when there is no history;
  - earlier months without history are **unknown**: `target_known=false`, and target/achievement/remaining are null, never 0;
  - user target first, then the contract target;
  - a percent target uses the collector's current unarchived debt, rounded to whole riyals half-up.

  RPT-19 is tested and reconciled **row for row** against `targets.achievement()`.
- **Protected employee fields.** The personal target settings (`user_target_kind/value/revision/from_month/record`) are `protected="target"`, `subject=user_id` (`core/user_privacy.py`).
  - The effective target, achievement and remaining stay visible, as they are on the legacy `AllCollectorsKPI` card and on `/v2/targets/achievement` for `dashbord_admin`.
  - This is a decision for the lead (§9).
- **Aggregate reports** read their dates, month, day, hours, mode, state, section and the RPT-24 contract **inside `build()`**. Those filters' own SQL is empty, because they act on the aggregation input rather than on the output rows. The framework documents this pattern.
- **Four roles** are marked on the columns (`owner`/`recorder`). RPT-19/22 show both actual figures:
  - `actual`: by the current owner, the v2 rule;
  - `actual_recorded`: by the recorder, the legacy KPI rule.

## 4. The reports

| Report | Rows | Filters (params) | Totals |
|---|---|---|---|
| RPT-17 | one per contract where I have current cases or recorded notes/payments/promises | `contract` | assigned (all/unarchived/archived, unarchived debt, not recovery), notes, payments recorded (count, amount, not recovery), approved/pending/rejected/reversed amount and count, promises and promised amount |
| RPT-18 | sections: assignments (unarchived/archived), classification (active/inactive/unknown), status, reachability (+ unspecified), approved receipts by contract; `position` ordinal | `activity_date_from/_to` (default today, max 366; notes use Riyadh day bounds, receipts civil dates) | assigned cases/unarchived/archived; notes total/active/inactive/unknown; receipts; approved. Status and reachability each partition the same notes (tested). |
| RPT-19 | one per collector in scope (active, with a contract) | `month` (YYYY-MM, default current), `contract`, `collector`, `target_source` | known targets, unknown count, actual (owner), payments, actual recorded |
| RPT-20 | (payment day, current owner, contract) groups; unassigned cases in their own group | `payment_date_from/_to` (default today, max 92), `owner`, `contract` | approved amount, receipts, of which unassigned |
| RPT-21 | (due day, current owner, contract) groups | `due_date_from/_to` (default today, max 92), `state` (open/overdue/fulfilled/cancelled, narrows before grouping), `owner`, `contract` | promised (not recovery), promises, amount and count per state, allocated evidence. The per-state amounts sum to the promised total (tested). |
| RPT-22 | one per collector in scope | `as_of` (month to date, default today), `contract`, `collector` | known targets, unknown count, assigned cases, recovered (recorder, legacy basis), recovered (owner) |
| RPT-23 | one per collector in scope (zeros kept), plus an "other / no agent" row in calls mode | `day`, `from_hour`/`to_hour` (default 8-17, inclusive Riyadh hours), `mode` notes\|calls, `contract`, `collector` | total, notes active/inactive/unknown, calls with billsec > 0, = 0, unknown, known billed seconds |
| RPT-24 | the summary row always first, then one section: daily `trend`, `contracts` (actual case contract; supervised contracts without cases included), `collectors` (configured contract) | `activity_date_from/_to` (default month to date, max 366), `contract` (inner, scoped selector), `section` | **always the summary**: cases now (all/unarchived/archived, unarchived debt), notes (and class), approved amount/count, collectors, contracts |
| RPT-25 | one per CDR source record (Asterisk CDR or call-center call) | `call_date_from/_to` (default today, max 31), `source`, `direction`, `pbx_status`, `attribution`, `duration` known/unknown, `recording` yes/no, `agent`, `owner`, `contract`, `number`, `account`, `q` | PBX answered, known and unknown durations with the known-only sum, known and unknown billsec with the known-only sum, with recording, distinct attributed cases, unmatched, ambiguous |

Details:
- **Positions and ranks.** RPT-19 `rank` is competition ranking by achievement %, then actual, with unknown targets last, the same as `_rank` in Phase 3. `contract_rank` ranks within the configured contract. The RPT-23 `position` is an ordinal over the whole scoped result, and filters do not renumber it.
- **Money never summed as recovery.** Debt appears only as case context: `unarchived_debt` and `percent_base`/`assigned_debt`, all labelled "not recovery".
- **No null totals.** The framework formats a null total as 0, so there is no "average group" or "highest group" total (RPT-20/21 legacy cards) and no "complete billable total". Totals that would be null when unknown are split into known-only sums plus unknown counts.

## 5. CDR: fixture, attribution, recordings

**Fixture.** `docker compose --profile cdr-test up -d cdr-test-db`:
- MySQL 8.0, `TZ=Asia/Riyadh`, tmpfs, `127.0.0.1:3317`;
- init SQL in `db/cdr-test/init/`, read-only user `crm` / `cdr-test-only`.

Schemas:
- `asteriskcdrdb.cdr` has the Issabel 4 / FreePBX column set. The legacy code reads `calldate, src, dst, duration, billsec, disposition, recordingfile` (plus `SELECT *`).
- `call_center.calls`, `call_recording` and `agent` have the columns of the legacy joins, plus the call-center module's columns.
- Collation `utf8mb4_general_ci` on purpose: MySQL 8's default collation folds Arabic-Indic digits in `LIKE`, which the Issabel MariaDB does not.

The 19 synthetic rows on 2026-09-20:
- they use the ZZ-TEST cases' numbers on `cawp-db` (`0551230001…06`, `966551239902`), so local dev shows calls on those cases, and the same numbers on the pytest cases;
- they cover the Riyadh 00:00 / 17:59:59 / 18:00 bounds and the neighbouring days;
- inbound and outbound calls;
- a shared and an unknown extension;
- a debtor with two cases, an archived case, a deleted contact, a "contains but doesn't end with" number, and unmatched numbers;
- call-center calls with **NULL duration**, one with no recording row and one with two recording rows;
- two rows for "today", for `GET /cdr`.

**Local dev** can point `cawp-api` at the fixture: set `CDR_HOST=cdr-test-db CDR_USER=crm CDR_PASSWORD=cdr-test-only CDR_DB=asteriskcdrdb CDR_ENABLED=1` in `CAWP-API/.env.docker` and restart `api`/`worker`. This is documented in `docker-compose.yml`.

**Tests** reach the fixture via `CDR_TEST_HOST` (default `cdr-test-db`):
- `CDR_TEST_HOST=10.10.0.220` fails the run;
- an unreachable fixture skips the 9 CDR tests with a message (checked);
- `CDR_ENABLED` stays 0 for every other test.

Run command:
```
docker compose --profile cdr-test up -d cdr-test-db
docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_repc -e CDR_TEST_HOST=cdr-test-db api-test \
    tests/test_reports_v2_c_support.py tests/test_reports_v2_c.py tests/test_reports_v2_c_cdr.py
```

**Reading.** `services/reports_v2_cdr.py` uses the same `Config.CDR_*` as `/cdr*`.
- It runs parameterised `calldate >= lo AND calldate < hi` over the Riyadh bounds, converted to the PBX wall clock (`CDR_TIMEZONE`, default Asia/Riyadh). That is the index-friendly form of the legacy `DATE(calldate) BETWEEN`.
- The `number` and `source` filters are pushed down.
- Call-center calls become one row per call; the legacy join repeats a call once per recording and drops calls without one.
- Limits:
  - more than `CDR_REPORT_MAX_ROWS` (default 50,000) per window returns 422 `cdr_window_too_large`;
  - an unreachable or disabled CDR returns 503 `cdr_unavailable`, and an export fails with `cdr_unavailable`;
  - previews and selectors reuse a window for `CDR_REPORT_CACHE_SECONDS` (30); exports always read fresh.
- **Unknown stays unknown:** a NULL call-center duration and the call center's missing billsec are null, and a real Asterisk 0 stays 0.

**Attribution** is computed per viewer, once per request, in a short read-only transaction just before the report snapshot:
- **CDR → case:** the legacy matching (a contact's last 9 digits), done the other way round. The last 9 digits of src and dst (Arabic-Indic digits converted to ASCII) are looked up among the last 9 digits of live contacts (index 0046), then matched to all of the debtor's cases, archived ones included.
  - `attribution = case`: exactly one match **in the viewer's scope**;
  - `ambiguous`: two or more in scope; no case, owner or contract is shown;
  - `unmatched`: no case anywhere.
- **CDR → agent:** the extension party is matched to the **current** `users.extension` of active users. A shared extension gives `agent_basis=ambiguous` and no agent, so the call is never counted for two people.
- **Case → collector:** the attributed case's **current owner**. The agent and the owner are separate columns.
- **Visibility:** a record is shown or counted only when it matches a case in the viewer's scope, or matches **no** case and the viewer is SuperAdmin or its agent is in the viewer's user scope. **A record whose number matches only out-of-scope cases is never shown or counted.**
  - Tested: a supervisor sees the shared-debtor call attributed to its own case, with `matched_cases=1` and no hint of the other case. The other team's owner sees the same call attributed to *its* case, and SuperAdmin sees it as ambiguous.
  - Number and owner filters and the selectors never reveal out-of-scope data.

**Recordings.**
- `has_recording` is metadata and always shown.
- `recording_file` and `recording_url` (`/cdr/recording?recordingPath=…`, the unchanged legacy route) are null unless the viewer holds `clints_callsRecords_download` or `_play`.
- The worker re-reads that right at generation: tested by revoking before export, which gives a file with empty links.

## 6. Reconciliation on `cawp-db` (legacy vs v2)

Method (`tests/reports_v2_c_parity.py reconcile`, read-only):
- the **real legacy handlers** of `api/dashboard.py` run in-process with a JWT for the actor;
- `_today()` is patched to the reconciled day where the handler only knows "today";
- the frontend calculations (`KPICollector`, `AllCollectorsKPI`, `CommentsReportDaily`) are ported to Python;
- SQL replicas categorize every differing row.

**Result: 35 comparisons, every difference in a named category, 0 unexplained.**

In the tables, **L** = legacy value and **v2** = the new report. A cell "L / v2" gives both.

**RPT-17 vs `/dashbord/user`** (all time). Every figure is identical. Legacy `paymentSum` is a float; v2 is exact.
- The categories are retired notes and legacy-deleted, not reversed payments. Both are 0 on `cawp-db`, and the test DB exercises both.
- Legacy counts every payment state as its `paymentSum`, so v2 also shows the approved figure.

| Actor | Cases L / v2 | Notes L / v2 | Payments L / v2 | L `paymentSum` (float) | v2 recorded | v2 approved |
|---|---|---|---|---|---:|---:|
| rasha.s | 3,467 / 3,467 | 33,959 / 33,959 | 2,016 / 2,016 | 6960549.149999999 | 6,960,549.15 | 6,941,822.59 |
| ghofran.b | 3,275 / 3,275 | 38,514 / 38,514 | 1,619 / 1,619 | 3924469.2299999995 | 3,924,469.23 | 3,916,651.24 |
| hanadi.a | 1,070 / 1,070 | 26,044 / 26,044 | 315 / 315 | 2231670.005 | 2,231,670.00 | 1,987,105.76 |
| tabby.1 | 7,996 / 7,996 | 0 / 0 | 3,581 / 3,581 | 1333520.0200000005 | 1,333,520.02 | 1,333,520.02 |
| marwan.a | 4,642 / 4,642 | 5,451 / 5,451 | 459 / 459 | 1737750.0699999994 | 1,737,750.07 | 1,701,442.33 |
| bader.b | 149 / 149 | 15,188 / 15,188 | 68 / 68 | 2355790.31 | 2,355,790.31 | 2,194,868.31 |

**RPT-18 vs `/dashbord/collector`.** The timestamps are those of a browser in Riyadh (Riyadh midnight).
- Assignments and notes are identical.
- Receipts differ only by **`+last civil day`**: legacy `payment_date BETWEEN start AND end`, where `end` is midnight of the last day, so it drops that whole day. v2 uses inclusive civil dates.

| Actor | Window | Notes L / v2 | Receipts L / v2 | Approved L / v2 | Explained by |
|---|---|---|---|---|---|
| rasha.s | 2026-09-01..15 | 911 / 911 | 50 / 53 | 178,278.20 / 203,078.20 | +last day 3 (24,800.00) |
| ghofran.b | 2026-09-01..15 | 916 / 916 | 19 / 21 | 70,017.65 / 81,517.65 | +last day 2 (11,500.00) |
| hanadi.a | 2026-09-01..15 | 875 / 875 | 20 / 22 | 241,461.87 / 242,817.87 | +last day 2 (1,356.00) |
| rasha.s | 2026-07-27 | 133 / 133 | 0 / 4 | 0.00 / 7,467.36 | +last day 4 (7,467.36) |
| ghofran.b | 2026-07-27 | 121 / 121 | 0 / 4 | 0.00 / 8,753.00 | +last day 4 (8,753.00) |
| hanadi.a | 2026-07-27 | 61 / 61 | 0 / 10 | 0.00 / 8,427.24 | +last day 10 (8,427.24) |
| rasha.s | 2026-08 | 1,971 / 1,971 | 88 / 95 | 617,626.93 / 629,451.93 | +last day 7 (11,825.00) |
| ghofran.b | 2026-08 | 2,953 / 2,953 | 103 / 115 | 177,501.39 / 194,700.39 | +last day 12 (17,199.00) |
| hanadi.a | 2026-08 | 2,044 / 2,044 | 78 / 82 | 191,946.79 / 195,066.34 | +last day 4 (3,119.55) |

**RPT-19.**
- **Against the Phase 3 service** (aidi.dev), every row is equal: target, actual, recorded, achievement, remaining and rank.
  - 2026-09: 122 rows, target 182,936,594.00, actual 3,448,822.04, recorded 3,744,293.89.
  - 2026-08: 122 rows, all 122 **unknown** (the history starts in September), target 0.00 known, actual 10,299,084.61.
- **Against the legacy `KPICollector`** (the own card, from `/dashbord/collector/v2`), the targets are equal:

  | Actor | Legacy target | v2 target | Legacy achieved (float) | v2 recorded | Explained by |
  |---|---:|---:|---:|---:|---|
  | rasha.s | 500,000 | 500,000.00 | 203078.19999999998 | 203,078.20 | float only |
  | hanadi.a | 200,000 | 200,000.00 | 243317.87000000002 | 243,317.87 | float only |
  | Ibrahim.m | 0 | 0.00 | 0 | 0.00 | none |
  | ghofran.b | 200,000 | 200,000.00 | 84854.65 | 57,612.00 | −27,742.65 recorded on a case now outside her scope (reassigned: the scope correction); +500.00 dated later this month (legacy stops at "now", the month is whole in v2) |

  Legacy restricts the KPI to the user's own contract, and v2 does not. That category exists but is 0 for these actors.
  Legacy achievement % is a float (`achieved / target × 100`); v2 is exact to 2 decimals, half-up, and null for a 0 target instead of 0.

**RPT-20 / RPT-21 vs the daily admin cards** (aidi.dev; `_today` patched):

| Report | Day | Groups L / v2 | L exact | v2 | Explained by |
|---|---|---|---:|---:|---|
| RPT-20 | 2026-07-27 | 35 / 34 | 432,967.69 | 515,720.15 | +645 receipts (209,536.62) stored 21:00Z on the eve (Riyadh civil day 27th; legacy `date()` is the UTC date); −413 (126,784.16) whose UTC date is the 27th but Riyadh date the 28th |
| RPT-20 | 2026-08-15 / 09-14 / 09-21 | 8/8, 36/36, 1/1 | 18,831.91 / 276,221.33 / 1,305.00 | same | none |
| RPT-21 | 2026-07-27 / 08-15 / 09-14 / 09-21 | equal | 347,365.14 / 70,964.75 / 362,253.11 / 59,651.96 | same | none; v2 splits them, e.g. 09-14 is all overdue |

The categories `−deleted promises`, `+unassigned cases` and `utc-date` are exercised in the test DB, and all were 0 on these `cawp-db` days apart from the ones shown.
The legacy float sums equal the exact sums to the halala.

**RPT-22 vs `/dashbord/Allcollector/v2` + `AllCollectorsKPI`** (aidi.dev, 2026-09-01..24): 122 / 122 rows.
- The legacy target total is 182,936,594.00, and the legacy per-user formula equals the v2 target for every user.
- Legacy achieved (recorder, browser month) is 3737562.89 (float) = v2 `recovered_recorded` 3,737,562.89 for every user.
- v2 also shows `recovered_owner` 3,442,091.04.

**RPT-23 (notes) vs `/dashbord/commentsReport/v2` + `CommentsReportDaily`** (aidi.dev, hours 08-17): every user is equal, with totals of 6,312 on 2026-09-14, 5,814 on 09-13 and 5,243 on 08-20.
- The legacy handler's day is the UTC date, but Riyadh 08:00-17:59 lies inside it, so no row differs.
- For supervisors with a partial scope, legacy shows **all** collectors (the admin dashboards are global), while v2 shows the scoped team. For example, marwan.a has 11 rows instead of 122. That is the scope correction.

**RPT-24 vs `/dashboard`** (aidi.dev, window of Riyadh-midnight timestamps): every total is equal.
- 2026-09-01..15: 473,957 cases (171,456 unarchived), 57,079 notes, 3,736,289.41 approved in 1,708 receipts.
- 2026-07-27: 5,213 notes and 515,720.15 approved.

Every contract's approved total is equal too. Legacy `clients` counts are unscoped for every caller; v2 is scoped.

**CDR (RPT-25, RPT-23 calls) against the legacy `/cdr*` code paths on the fixture** (`Issable_db` in-process, tested):

| Legacy path | Case / check | Legacy rows | v2 rows | Difference |
|---|---|---:|---:|---|
| `/cdr_by_date` (`GetCombinedCalls`) | A1's contacts | 8 | 6 | −1 "contains, does not end with" (`05512300011`); −1 call-center call 5 listed twice (two recordings) |
| `/cdr_by_date` | A2's contact | 5 | 5 | none |
| `/cdr_by_date` | A3 (contact stored in Arabic-Indic digits) | 0 | 2 | +2: legacy LIKEs Arabic digits against ASCII CDR text. One call has no recording row, which the legacy INNER JOIN drops too. |
| `/cdr` (`GetSubscription`) | today | 2 | 2 | none |
| `/dashbord/commentsReport/v2` calls (`GetCallsByDate` + `CallsReportDaily`, `src === extension`, 08-17) | per user, 2026-09-20 | col1 9, col2 1, 7103 counted for **both** holders | col1 9, col2 1, 7103 in "other / no agent" | double count removed |

v2 also lists call-center calls without a recording row or agent (the legacy INNER JOINs drop them), and gives each call its own row.

## 7. Tests

`docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_repc -e CDR_TEST_HOST=cdr-test-db api-test -q tests`
- **Stream R-C: 46 tests**: support 1, reports 34, CDR 11. All pass with the fixture up. Without it, 9 CDR tests skip and 2 run.
- **Whole suite: 771 passed, 0 failed** (172 s, with streams A and B's files present, fixture up).

| Area | What is proven |
|---|---|
| Keys | the catalogue per role; 403 with `required_any` for every report and for its export; calls mode 403 on preview, selector **and export creation**; the recording keys alone give 403 on RPT-25 |
| Definitions | defaults and semantics (`riyadh_day_bounds`, hours 8-17, mode single, month current, `max_days`), protected and role flags, `depends_on` |
| Validation | 13 cases: bad month, day, hours, reversed hours, unknown mode/state/section, two modes, range too long (RPT-20/24/25), reversed range, bad id |
| Recorder rule | RPT-17 exact totals by contract and status; reassignment moves the assignment but keeps the notes/payments with the recorder; legacy `/dashbord/user` differs only by the retired note |
| Riyadh bounds | RPT-18: 00:00, 23:59:59 the day before, 18:00; RPT-20: 21:00Z eve in, 20:59:59Z in, 21:00Z out; RPT-23 hours 00/18 outside the default window, all-day window; RPT-25: 00:00:00, 17:59:59, 18:00:00, previous/next day |
| Exact totals | RPT-17/18/20/21/22/24/25 totals as exact strings; RPT-21 per-state amounts sum to the promised total; a fulfilled promise via auto-allocation |
| Targets | RPT-19 June: personal amount, contract percent of the current debt (10 % of 23,000), contract amount fallback, zero target → achievement null, ranks and contract ranks; **equal to `targets.achievement()` for every collector**; **`known:false`** before any history; July carries June forward |
| Scope | supervisor rows = contract A users, and money from out-of-scope cases excluded; collector = own row; RPT-20/24 scope; RPT-24 contract selector scoped; RPT-23 notes on out-of-scope cases excluded |
| Protected fields | personal target settings null for a supervisor without user keys, visible on the own row and to SuperAdmin; the same in the export (the worker reads rights fresh) |
| CDR attribution | 20 records for SuperAdmin with every attribution, agent basis and direction checked; supervisor 18 (the B1-only call and the no-agent unmatched call hidden); col2 3; the shared-debtor call per viewer; number/owner filters and the owner selector leak nothing; reassigning a case makes its call visible |
| Unknown durations | call-center duration/billsec null, `duration=unknown` filter, known-only sums, a real Asterisk 0 stays 0 |
| Recordings | links only with download/play; granting download shows the file; revoked before export → empty links in the file |
| Robustness | 503 `cdr_unavailable` (preview) and failed/`cdr_unavailable` (export); 422 `cdr_window_too_large`; identical attribution with and without index 0046; the SQL phone key equals the Python key; the plan uses `ix_clintcontacts_phone_key` |
| Export = preview | full CSV equals every preview cell in definition order for RPT-17, 18, 19, 22, 23, 24, 25; totals and row counts equal; XLSX runs; `xlsx_ar` is refused |
| Legacy code paths | `/dashbord/user`, `/dashbord/collector`, `/dashbord/admin/dailyPayments/v2`, `/dashbord/admin/dailyPaymentDates/v2` (in the test app), and `Issable_db.GetCombinedCalls`, `GetSubscription`, `GetCallsByDate` on the fixture |

## 8. Timings on `cawp-db` (474k cases, 1.8M contacts; read-only; median of 5)

"Preview" = page 1 plus the exact totals in one snapshot. "Export" = the full CSV through the worker's `generate()` in a fresh snapshot.

| Report, actor, window | Rows | Preview | Export (bytes) |
|---|---:|---:|---:|
| RPT-17 rasha.s (34k notes, 2k payments) / aidi.dev | 6 / 1 | 106 / 17 ms | 107 ms |
| RPT-18 rasha.s, 15 days | 25 | 10 ms | 11 ms |
| RPT-19 aidi.dev / marwan.a / rasha.s, 2026-09 | 122 / 11 / 18 | 150 / 17 / 19 ms | 151 ms |
| RPT-20 aidi.dev / bader.b, July 2026 | 569 / 98 | 47 / 26 ms | 59 ms (66 KB) |
| RPT-21 aidi.dev, September | 533 | 36 ms | 44 ms (79 KB) |
| RPT-22 aidi.dev, as of 09-15 | 122 | 188 ms | 188 ms |
| RPT-23 notes aidi.dev / qasim.s | 122 / 1 | 51 / 4 ms | 50 ms |
| RPT-24 aidi.dev summary / collectors, 15 days | 1 / 194 | 602 / 589 ms | 890 / 874 ms |
| RPT-24 aidi.dev trend, **one year** (761k notes) | 366 | 1,943 ms | 2,278 ms |
| RPT-24 marwan.a summary | 1 | 440 ms | 627 ms |
| **RPT-25** aidi.dev / marwan.a / rasha.s, **20,000-call day**, without 0046 | 17,378 / 5,228 / 6,570 | 5.35 / 5.32 / 5.32 s | 5.7 s (4.6 MB) |
| **RPT-23 calls**, same day, without 0046 | 123 | 5.60 s | 5.64 s |

RPT-25 without index 0046 breaks down as MySQL read 174 ms + attribution 4.5 s (a sequential scan of the 1.8M contacts) + the two report statements about 0.65 s.
- **With 0046**, measured on an indexed throwaway copy of the contact/case columns: attribution 0.41 s for the 15.9k distinct numbers (25 ms for 500). The expected preview is therefore about 1.2 s for 20k calls, and far less for a normal team window.
- The index takes 2.9 s to build (CONCURRENTLY) and is 18 MB.
- `attribute()` picks per-key btree probes when a valid index exists, and a hashed `= ANY` scan otherwise; a test shows both give the same results.
- The synthetic day (numbers sampled from `cawp-db` contacts) was loaded into the throwaway fixture only and deleted afterwards. The indexed copy was built on the test server and dropped.

Optimizations kept:
- RPT-24 makes one `clints` pass, with notes and receipts pre-aggregated (collectors section 1.3 s → 0.59 s).
- The attribution runs once per request, outside the two report statements. Before that, the page query's plan did not finish within 10 minutes.

## 9. Decisions for the lead

1. **Recorder rule for RPT-17 and RPT-18**, as for RPT-07: my recorded work, whatever the current owner, with no case scope. Assignments are my current cases.
2. **RPT-19 keys** `dashbord_user` + `dashbord_admin`, the `team_any` precedent. Collectors see only their own row, so the legacy global top-six is **not** reproduced; its "disclosure defect" is corrected per the rebuild.
3. **Target privacy.** The personal target settings are protected. The effective target, achievement and remaining stay visible to report holders, as on the legacy KPI card and `/v2/targets/achievement`. Strict alternative: protect the effective values too, in which case مشرف2 (7 users, no user keys) would lose targets that legacy shows them.
4. **The percent base** is all of the collector's current unarchived debt, not only the in-scope part, because the target must not depend on the viewer. It is shown as `percent_base`/`assigned_debt` (legacy `AllCollectorsKPI` shipped every collector's cases too).
5. **Unassigned cases** have their own group in RPT-20/21. Legacy dropped them through an inner join. It is 0 on `cawp-db` in the windows checked.
6. **RPT-23 calls** counts outbound Asterisk records only, which is the legacy rule. Call-center calls are also Asterisk records, so adding them would double-count. RPT-25 lists both families with `source`.
7. **CDR assumptions to verify at commissioning (5a):** the PBX wall clock is Asia/Riyadh (`CDR_TIMEZONE`), and the `call_center.agent.number` column exists (it is read in addition to the legacy `a.name`).
8. **Attribution rule:** a suffix match instead of the legacy "contains" (one fixture difference), and Arabic-Indic contacts do match. Unmatched calls are visible to SuperAdmin and to the supervisor/collector of the calling extension.

## 10. Open items

1. **Lead:**
   - apply **0046** (`manage_db.py up`; CONCURRENTLY, about 3 s, no blocking lock);
   - add it to `db/README.md` (not my file);
   - restart `api`/`worker` so the registry discovers `reports_v2_c`.

   **0047 is unused.**
2. **`CAWP-API/.env.docker` still has `CDR_HOST=10.10.0.220`** with CDR enabled. The legacy dashboards already try that address from local dev, and RPT-23 calls / RPT-25 would too. Recommendation: `CDR_HOST=cdr-test-db` (with the fixture) or `CDR_ENABLED=0` locally. Not my file.
3. **`GET /cdr/recording` is JWT-only** (`app.py`): any signed-in user can fetch any path under `RECORDINGS_ROOT`. The report shows the link only to download/play holders, but the route should check `clints_callsRecords_download | clints_callsRecords_play`, and that the path belongs to a CDR record visible to the caller. `GET /cdr` and `/cdr_by_date` are also JWT-only and unscoped. These are `app.py` changes (lead).
4. **Framework needs** (`core/reports`, lead):
   - nullable totals (average/highest group, "complete billed seconds unavailable"): today a null total is written as 0, so these totals are omitted;
   - an optional CSV footer row, like the legacy dashboard CSV "Total";
   - a report hook for external sources (a temp table inside the snapshot) instead of binding up to 50k CDR rows as literal arrays. psycopg2 inlines them, so a 20k-call window is about 5-7 MB of SQL text, parsed twice for totals and page.
5. **Legacy findings (unchanged):**
   - `/dashbord/admin/*`, `/dashbord/commentsReport/v2` and the `/dashbord/collector/v2` top-six are global for any signed-in user (only `Allcollector` is gated);
   - their CSV exports are not formula-safe;
   - their "today" is the server's UTC date;
   - `/dashbord/collector` misses the last day;
   - `CallsReportDaily` double-counts shared extensions;
   - `KPICollector` counts the own contract only.
6. **Final full suite:** 771 passed, 0 failed, with every stream's files present.
