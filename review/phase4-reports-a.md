# Phase 4, stream R-A: reports RPT-01 … RPT-05

Date: 2026-09-24. Branch `phase-4-reports`, not committed. Scope: stream R-A in `docs/phase4-spec.md`.

- **No migration.** 0042 and 0043 are unused (§6 explains why no index was needed).
- No container was restarted.
- Every measurement on `cawp-db` ran on read-only connections (`default_transaction_read_only=on`) and in the framework's `REPEATABLE READ READ ONLY` snapshot. Nothing was written to `cawp-db`.
- No framework file (`core/reports/*`) was changed. Framework notes for the lead are in §8.

## 1. Files

| Area | File |
|---|---|
| Reports | `CAWP-API/services/reports_v2_a.py`: `REPORTS = (RPT-01, RPT-02, RPT-03, RPT-04, RPT-05)` plus five selector entities |
| Reconciliation and timings | `CAWP-API/services/reports_v2_a_perf.py`: read-only harness, no `REPORTS`. Run it with `docker exec -w /app cawp-api python -m services.reports_v2_a_perf reconcile\|timings [--json f]` |
| Tests | `CAWP-API/tests/test_reports_v2_a.py`: 18 tests |
| Test fixtures | `CAWP-API/tests/fixtures/seed_reports_a.sql`: UUID prefix `ea0ea0ea-…` (see §8.4) |

## 2. Reports, keys and formats

All five legacy screens exported a right-to-left ExcelJS workbook with Arabic headers, so **every report offers CSV, XLSX and Arabic XLSX**. The legacy exporters are:
- `components/shared/myComponents/exportUsers.tsx` (sheet "clints", file `المستخدمين.xlsx`);
- `views/clients/list/components/export.tsx`;
- `exportClientArchive.tsx` ("عملاء الأرشيف");
- `exportComment.tsx` ("المتابعات");
- `exportCurrentUserComment.tsx`.

**Screen-key rule.** Each report uses its legacy screen's route key.
- Route keys: `CAWP-frontend/src/configs/routes.config/routes.config.ts`.
- The sidebar (`configs/navigation.config/index.ts`) and the role editor (`views/permissions/form/Fields.tsx`) use the same keys.

| Report | Legacy API → screen | Key: routes.config.ts line (navigation, Fields.tsx lines) | Arabic sheet |
|---|---|---|---|
| RPT-01 users roster | `GET /uesrs` (app.py:678, JWT-only) and `GET /uesrs/export` (api/reports.py:1033, `users`) → `/users` | `users`: :16 (nav :398, Fields :89) | المستخدمين |
| RPT-02 unarchived cases | `GET /clints` (api/clients_reports.py:149) and `GET /clients/export` (:168) → `/clients` | `clints`: :108 (nav :65, Fields :122) | العملاء |
| RPT-03 archived cases | `GET /clints/archive/` (:188) and `GET /clients/archive/export` (:202) → `/archive` | `clints_archive`: :130 (nav :98, Fields :165) | عملاء الأرشيف |
| RPT-04 team note activity | `GET /comments` (api/reports.py:256) and `/comments/export` (:278) → `/report/users/comments` | `report_users_comments`: :246 (nav :217, Fields :383) | المتابعات |
| RPT-05 my notes | `GET /comments/current_user` (:304) and its export (:330) → `/report/collectors/comments` | `report_collectors_currentUser_comments`: :321 (nav :335, Fields :430) | متابعاتي |

Who holds these keys on `cawp-db`:
- the 253 collectors (محصل) hold `clints` and `report_collectors_currentUser_comments`, so they see RPT-02 and RPT-05;
- مشرف/مشرف2 hold `clints`, `clints_archive` and `report_users_comments`;
- ADMIN, عمليات, مدير مشاريع and مدير تحصيل hold all five keys;
- معالجة holds `users` and `report_collectors_currentUser_comments`.

**Every role that holds `users` also holds `users_details`.** This matters for RPT-01 (§3.1).

## 3. Definitions

The four roles are always separate columns:
- current case owner (`owner_*`);
- note recorder (`recorder_*`);
- archive actor (`archived_by_name`);
- the employee (RPT-01).

Money is exact `numeric`, rounded per record. It is a string in JSON and CSV and a `#,##0.00` number in XLSX.

### 3.1 RPT-01 users roster (`users`, family `users`)

**Employee scope** (who appears in the roster):
- **SuperAdmin, or a holder of `users_details`/`users_edit`: every employee.** These are the identity-administration keys. Their holders can already open any user record (`GET /users/<id>`, app.py:769, `required_any('users_details','users_edit')`) and see every protected field (`core/user_privacy.py`). A narrower roster would protect nothing.
- **Anyone else sees only linked employees:** themselves, their supervised users (`supervisor_users`), employees whose own contract (`users.contract_id`) they supervise, and the current owners of cases in their current case scope.
  - Employees without cases are included when they are linked this way.
  - Historical owners and recorders are not links.

**Assignment totals.** `case_count` and `original_debt` count only the cases in the caller's **current case scope**. This is evaluated independently of the employee scope.
- `case_state` (`unarchived` or `archived`; default all, as in legacy `customeFilterArchivingStatusesValue[]`) picks which cases are counted.
- `unarchived_case_count` and `archived_case_count` are always shown.

**Protected fields** (framework `protected=`, following `core/user_privacy.py`):
- `email`, `mobile`, `identity_number`: `identity`;
- `target_is_percentage`, `target_amount`, `target_percent`: `target`;
- the employee's own row is never redacted.

**No filter, sort or total touches a protected field.** `q` searches the name, login and extension only, and there is no target total. So no query can reveal a hidden value.

| | |
|---|---|
| Columns | user_id, name, user_name, role, status (enum نشط/غير نشط, the legacy export's words), branch, nationality, contract (the user's own), extension, supervisors, created_at, email\*, mobile\*, identity_number\*, target_is_percentage\*, target_amount\*, target_percent\*, case_count, original_debt, unarchived_case_count, archived_case_count (\* = protected) |
| Filters | status (active/inactive), case_state, role, branch, contract (Ids with scoped selectors), created (Riyadh civil dates), q |
| Sorts | created_at (default ascending, as legacy `order_by(User.created_at)`), name, user_name, role, branch, case_count, original_debt |
| Totals | count, active_count, inactive_count, with_cases_count, case_count, original_debt |

### 3.2 RPT-02 unarchived / RPT-03 archived cases (`clints` / `clints_archive`, family `cases`)

- **Rows:** one row per case, in the current case scope. RPT-02 has `is_archive = false` and RPT-03 has `is_archive = true`. Unassigned cases in scope are included.
- **Latest note:** the case's latest effective note, read from `clints.last_comment_id`. This is the same source the legacy grids use, and `comments_last_comment_trg` keeps it on the newest non-retired note.
- **Money** follows `services/money.case_balance`:
  - approved = approved, not deleted, not reversed;
  - pending = approval NULL;
  - outstanding = original − approved;
  - `payment_count` counts every record, reversed ones included.
- **Assignment:**
  - `assigned_at` is the latest non-deleted `SepcifyUsers` row, **only when it assigned the case to its current owner**;
  - `assignment_days` is the number of whole 24-hour periods since then, measured at the snapshot time;
  - otherwise both are null (unknown, never 0). On `cawp-db` only the one unassigned live case is unknown.
- **Archive fields (RPT-03):** from the case's current archive record (`clints."clintArchive_id"`). After a restore and a re-archive, the new record is shown. An archived case without a record is **kept**, with null archive fields.
- **Attributes:** `provision` (البروفجن) and `product` (المنتج) are the raw case attributes. They are null when absent; the legacy export wrote 0.

| | |
|---|---|
| Columns (legacy order first) | case_id, portfolio_name, portfolio_created_on, contract_name, [RPT-03: archived_by_name, archive_reason, archived_at], owner_name, owner_user_name, owner_branch, assigned_at, assignment_days, last_note_at, last_note_status, last_note_reachability, account_number, customer_name, identity_number, original_debt, approved_amount, pending_amount, outstanding_amount, payment_count, delinquency_reason, product, provision, locked, case_created_at |
| Filters | contract, portfolio (depends on contract), owner, owner_branch, assignment (assigned/unassigned), [RPT-03: archived (Riyadh days), archived_by, archive_reason], note_status (latest note), has_note, last_note (Riyadh days), created, portfolio_created, locked, debt (exact SAR range), account (literal contains), q (customer, account, identity) |
| Sorts | case_created_at (default descending, as legacy), original_debt, outstanding_amount, account_number, customer_name, last_note_at, owner_name, portfolio_name, contract_name, [RPT-03: archived_at] |
| Totals | count, original_debt (once per case), approved_amount, pending_amount, outstanding_amount, unassigned_count, locked_count, with_payments_count |

### 3.3 RPT-04 team notes / RPT-05 my notes (family `notes`)

- **Effective notes only.** Retired v2 notes and legacy-deleted notes (`is_deleted`) are excluded. A corrected note shows its effective text and `corrected = true`; its recorder and date are unchanged.
- **Dates:** `created` is a **required** range of Asia/Riyadh civil days, as legacy also required a date pair. RPT-04 accepts at most 366 days.
- **Original debt:** the case's `original_debt` is per-row context and is **never totalled**. Legacy RPT-05 `totalAmount` summed it once per note; v2 gives note and distinct-case counts instead.
- **RPT-04 scope:** the current case scope.
- **RPT-05 scope (recorder rule):** `recorder = me` with **no case-scope predicate**. The caller's own notes stay after the case is reassigned, including in the worker's export. `recorder` is not a parameter of RPT-05.
- **RPT-04 `note_selection=latest_per_case`** (`all` when not set):
  1. `build()` applies the scope and **every other set filter** inside the selection, using the framework's own filter SQL and parameter names;
  2. it keeps one note per case (newest `create_at`, then highest id; the display sort never matters);
  3. counting, paging and the export then run on that set.

  So this is the latest **matching** note across the full filtered result. The legacy JS deduplicated only the fetched page, or the whole export.

| | |
|---|---|
| Columns | note_id, created_at, note_hour (Riyadh; the legacy export had the hour), recorder_name, recorder_user_name, recorder_branch, note, corrected, status, reachability, contact_method, contact_number, callback_at, case_id, account_number, customer_name, identity_number, customer_mobile (the attribute mobile_1..3 or MSISDN, as the legacy export), contract_name, portfolio_name, original_debt, owner_name, case_archived |
| Filters | created (required), [RPT-04: note_selection, recorder, recorder_branch], contract, portfolio, status, reachability, contact_method, callback (yes/no), case_state, text (literal note contains), account, q |
| Selectors | They depend on `created`, so a selector lists only values seen in the window. The frontend must send `created_from`/`created_to` with them. |
| Sorts | created_at (default descending, as legacy), account_number, customer_name, status, callback_at, [RPT-04: recorder_name] |
| Totals | count, case_count, [RPT-04: recorder_count], callback_count |

## 4. Reconciliation with the legacy endpoints on `cawp-db`

**Method** (`python -m services.reports_v2_a_perf reconcile`, read-only):
1. The **real legacy handlers** run in-process, with a JWT for each actor.
2. An SQL replica of each legacy predicate must equal the endpoint's own count. **It did in all 64 comparisons.**
3. The v2 report runs for the same actor and window.
4. Every differing row is placed in a named category. Each row's check is "legacy − legacy-only + v2-only = v2" (and for RPT-01, the same for the case count and the exact debt).

**Result: 64 comparisons, 0 unexplained, `other` = 0.**

| Category | Meaning |
|---|---|
| `−case scope` | **Scope correction.** Legacy has no case scope on `/clints/archive/`, `/comments` and the `/uesrs` counts. v2 limits rows (and RPT-01 totals) to the actor's current case scope. |
| `−employee scope` | Legacy lists every user. v2 lists linked employees unless the viewer is an identity administrator (§3.1). |
| `+Riyadh first day` | **Riyadh days.** A note created between 21:00 and 24:00 UTC the evening before the window is on the window's first Riyadh day. Legacy UTC days miss it. |
| `−UTC last evening` | A note created between 21:00 and 24:00 UTC on the last day is on the *next* Riyadh day. Legacy UTC days include it. |
| `+no archive record` | An archived case whose `clintArchive_id` is NULL. The legacy inner join drops it; v2 keeps it with unknown archive fields. |
| rounding per case | 340 cases have more than 2 decimals in `clints.amount`. v2 sums `round(amount, 2)` per case, as `money.case_balance` does; legacy sums raw doubles. |

Actors:
- **aidi.dev**: ADMIN, supervisor of all 15 contracts.
- **maher.j**: 12 contracts.
- **s.almalki**: 13 contracts.
- **Tamara**: معالجة, collector scope, 97,571 own cases.
- **bader.b, marwan.a, qasim.s**: مشرف2, the same actors as R0.
- **rasha.s, asma.k, ghofran.b**: the collectors with the most notes.

### RPT-01 vs `GET /uesrs/export`

This has the same `count_clints`/`total_amount` computation as `GET /uesrs`.

| Actor | Cases counted | Legacy users | Legacy Σ count_clints | Legacy Σ total_amount (float) | Legacy Σ debt (exact) | v2 users | v2 case_count | v2 original_debt | Differences |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| aidi.dev | all | 289 | 469,104 | 2761452930.3957796 | 2,761,452,930.59 | 289 | 469,104 | 2,761,452,930.59 | none |
| aidi.dev | unarchived | 289 | 171,455 | 696936822.4358997 | 696,936,822.63 | 289 | 171,455 | 696,936,822.63 | none |
| aidi.dev | archived | 289 | 297,649 | 2064516107.959879 | 2,064,516,107.96 | 289 | 297,649 | 2,064,516,107.96 | none |
| maher.j | all | 289 | 469,104 | 2761452930.3957796 | 2,761,452,930.59 | 289 | 441,229 | 2,755,057,816.36 | −case scope 27,875 (6,395,114.23) |
| maher.j | unarchived | 289 | 171,455 | 696936822.4358999 | 696,936,822.63 | 289 | 143,580 | 690,541,708.40 | −case scope 27,875 (6,395,114.23) |
| maher.j | archived | 289 | 297,649 | 2064516107.9598787 | 2,064,516,107.96 | 289 | 297,649 | 2,064,516,107.96 | none |
| s.almalki | all | 289 | 469,104 | 2761452930.395779 | 2,761,452,930.59 | 289 | 441,231 | 2,755,397,863.43 | −case scope 27,873 (6,055,067.16) |
| s.almalki | unarchived | 289 | 171,455 | 696936822.4358997 | 696,936,822.63 | 289 | 143,582 | 690,881,755.47 | −case scope 27,873 (6,055,067.16) |
| s.almalki | archived | 289 | 297,649 | 2064516107.959879 | 2,064,516,107.96 | 289 | 297,649 | 2,064,516,107.96 | none |
| Tamara | all | 289 | 469,104 | 2761452930.395779 | 2,761,452,930.59 | 289 | 97,571 | 18,357,925.04 | −case scope 371,533 (2,743,095,005.55) |
| Tamara | unarchived | 289 | 171,455 | 696936822.4358997 | 696,936,822.63 | 289 | 64,665 | 11,748,661.30 | −case scope 106,790 (685,188,161.33) |
| Tamara | archived | 289 | 297,649 | 2064516107.9598784 | 2,064,516,107.96 | 289 | 32,906 | 6,609,263.74 | −case scope 264,743 (2,057,906,844.22) |

- **The legacy exact debt matches.** The "Legacy Σ debt (exact)" column is per-case rounded, and it equals v2 where the scopes are equal. The raw double sum differs by the 340 over-precise amounts (for example 2,761,452,930.3958 raw vs .59 rounded).
- **All four actors are identity administrators**, so all 289 users appear and only the case totals narrow. Legacy ignores scope when counting.

**The scoped roster (informational).** No role on `cawp-db` holds `users` without `users_details`, so the scoped path cannot be reconciled against a real v2 user. For the three مشرف2 supervisors, which have **no `users` key** (v2 answers 403), the harness ran the engine directly to show what the scoped roster would return if they were granted `users`.
- Legacy `GET /uesrs` is JWT-only, so today these supervisors can read all 289 users and every user's case totals.
- This is the Phase 3 open item "GET /uesrs still embeds each role's full permission JSON".

| Actor | Legacy GET /uesrs users | v2 employee scope | Legacy Σ count_clints | v2 case_count | −employee scope | −case scope |
|---|---:|---:|---:|---:|---:|---:|
| bader.b | 289 | 70 | 469,104 | 12,976 | 219 | 456,128 |
| marwan.a | 289 | 41 | 469,104 | 128,350 | 248 | 340,754 |
| qasim.s | 289 | 2 | 469,104 | 945 | 287 | 468,159 |

### RPT-02 vs `GET /clints` (same scope rule: no difference in rows)

| Actor | Variant | Legacy countRow | Legacy totalAmount (float) | Legacy Σ (exact, raw) | v2 rows | v2 original_debt | v2 approved | v2 pending | v2 outstanding | Differences |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| aidi.dev | all | 171,456 | 696937122.435904 | 696,937,122.44 | 171,456 | 696,937,122.63 | 18,856,921.09 | 112,193.43 | 678,080,201.54 | rounding per case +0.1941 |
| aidi.dev | contract=أيجارة | 4,921 | 155944541.0299999 | 155,944,541.03 | 4,921 | 155,944,541.03 | 9,681,122.11 | 38,688.75 | 146,263,418.92 | none |
| maher.j | all | 143,581 | 690542008.2059047 | 690,542,008.21 | 143,581 | 690,542,008.40 | 18,854,921.09 | 112,193.43 | 671,687,087.31 | rounding per case +0.1941 |
| bader.b | all | 3,180 | 138379761.77999997 | 138,379,761.78 | 3,180 | 138,379,761.78 | 4,122,016.92 | 54,400.35 | 134,257,744.86 | none |
| marwan.a | all | 66,921 | 80997831.12999992 | 80,997,831.13 | 66,921 | 80,997,831.13 | 1,791,407.43 | 9,498.69 | 79,206,423.70 | none |
| qasim.s | all | 395 | 19519444.530000005 | 19,519,444.53 | 395 | 19,519,444.53 | 915,539.13 | 0.00 | 18,603,905.40 | none |
| rasha.s | all | 67,874 | 291515290.4799988 | 291,515,290.48 | 67,874 | 291,515,290.48 | 1,123,533.38 | 9,198.69 | 290,391,757.10 | none |
| Tamara | all | 64,665 | 11748661.300000062 | 11,748,661.30 | 64,665 | 11,748,661.30 | 48,806.84 | 8,698.69 | 11,699,854.46 | none |

The rows are identical. For aidi.dev, RPT-02 has 171,456 cases against RPT-01's 171,455 unarchived: the difference is the single **unassigned** live case, which RPT-02 lists and RPT-01 cannot attribute to an employee.

### RPT-03 vs `GET /clints/archive/` (legacy: **unscoped**)

| Actor | Legacy countRow | v2 rows | v2 original_debt | v2 approved | v2 outstanding | −case scope (rows, debt) | +no archive record |
|---|---:|---:|---:|---:|---:|---|---:|
| aidi.dev | 302,499 | 302,501 | 2,065,236,223.10 | 105,518,566.65 | 1,959,717,656.45 | 0 | 2 |
| maher.j | 302,499 | 302,501 | 2,065,236,223.10 | 105,518,566.65 | 1,959,717,656.45 | 0 | 2 |
| bader.b | 302,499 | 9,796 | 527,283,561.29 | 31,268,057.19 | 496,015,504.10 | 292,704 (1,537,898,687.26) | 1 |
| marwan.a | 302,499 | 61,433 | 690,462,855.38 | 30,266,763.89 | 660,196,091.49 | 241,066 (1,374,712,936.46) | 0 |
| qasim.s | 302,499 | 550 | 31,169,993.18 | 2,451,963.12 | 28,718,030.06 | 301,950 (2,034,012,255.37) | 1 |

- **Legacy has no scope.** `services/clients_query.build_archive_query` receives the identity and never uses it, so every holder of `clints_archive` sees all 302,499 archived cases.
- **47 cases have two archive rows written milliseconds apart** (a double submit on 2026-09-13, same actor and reason). The pointer is used, so no case is duplicated.
- Legacy archive rows have no totals; v2 adds exact debt, approved, pending and outstanding.

### RPT-04 vs `GET /comments` (legacy: **unscoped**, UTC days)

| Window | Actor | Legacy countRow | v2 rows | v2 cases | +Riyadh first day | −UTC last evening | −case scope | other |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-08-01..08-31 | aidi.dev | 149,150 | 149,138 | 76,547 | 1 | 13 | 0 | 0 |
| 2026-08-01..08-31 | maher.j | 149,150 | 149,138 | 76,547 | 1 | 13 | 0 | 0 |
| 2026-08-01..08-31 | bader.b | 149,150 | 12,033 | 2,803 | 0 | 0 | 137,117 | 0 |
| 2026-08-01..08-31 | marwan.a | 149,150 | 31,863 | 14,985 | 0 | 0 | 117,287 | 0 |
| 2026-08-01..08-31 | qasim.s | 149,150 | 812 | 227 | 0 | 0 | 148,338 | 0 |
| 2026-07-27 (one day) | aidi.dev | 5,217 | 5,213 | 4,698 | 0 | 4 | 0 | 0 |
| 2026-07-27 | maher.j | 5,217 | 5,213 | 4,698 | 0 | 4 | 0 | 0 |
| 2026-07-27 | bader.b | 5,217 | 469 | 415 | 0 | 0 | 4,748 | 0 |
| 2026-07-27 | marwan.a | 5,217 | 985 | 754 | 0 | 0 | 4,232 | 0 |
| 2026-07-27 | qasim.s | 5,217 | 47 | 40 | 0 | 0 | 5,170 | 0 |
| 2025-09-01..2026-08-31 | aidi.dev | 761,465 | 761,452 | 133,377 | 0 | 13 | 0 | 0 |
| 2025-09-01..2026-08-31 | maher.j | 761,465 | 761,449 | 133,375 | 0 | 13 | 3 | 0 |
| 2025-09-01..2026-08-31 | bader.b | 761,465 | 192,894 | 10,493 | 0 | 0 | 568,571 | 0 |
| 2025-09-01..2026-08-31 | marwan.a | 761,465 | 138,976 | 32,642 | 0 | 0 | 622,489 | 0 |
| 2025-09-01..2026-08-31 | qasim.s | 761,465 | 15,602 | 664 | 0 | 0 | 745,863 | 0 |
| 2026-09-01..09-30 | aidi.dev | 57,109 | 57,122 | 37,278 | 13 | 0 | 0 | 0 |
| 2026-09-01..09-30 | maher.j | 57,109 | 57,122 | 37,278 | 13 | 0 | 0 | 0 |
| 2026-09-01..09-30 | bader.b | 57,109 | 5,405 | 2,327 | 0 | 0 | 51,704 | 0 |
| 2026-09-01..09-30 | marwan.a | 57,109 | 14,862 | 9,327 | 0 | 0 | 42,247 | 0 |
| 2026-09-01..09-30 | qasim.s | 57,109 | 645 | 286 | 0 | 0 | 56,464 | 0 |

- **Scope.** Legacy `/comments` has no scope, so every supervisor saw every team's notes. v2 limits them to the current case scope.
- **Precedence.** A row both out of scope and on a boundary evening counts as `−case scope`.

**Latest note per case (August 2026).** Legacy has no server-side option: the `latestOnly` toggle deduplicated the fetched rows in the browser, keeping the first row met on a tie. The legacy count below is the number of distinct cases in the legacy row set, which is what that deduplication yields over the full export.

| Actor | Legacy distinct cases | v2 `latest_per_case` rows | Cases with a tied latest note |
|---|---:|---:|---:|
| aidi.dev / maher.j | 76,546 | 76,547 | 0 |
| bader.b | 76,546 | 2,803 | 0 |
| marwan.a | 76,546 | 14,985 | 0 |
| qasim.s | 76,546 | 227 | 0 |

- **aidi.dev's +1 is the Riyadh first day.** A set comparison gives 1 case only in v2 (its only in-window note is the first-day note) and 0 only in legacy; the 13 last-evening notes are on cases that have other August notes.
- `v2 latest_per_case` rows always equal the v2 distinct-case count, so there is one note per case.

### RPT-05 vs `GET /comments/current_user`

Both sides use recorder = me with no case scope, so only the day bounds could differ.

| Window | Actor | Legacy countRow | Legacy totalAmount (float: debt summed per note) | v2 rows | v2 cases | Differences |
|---|---|---:|---:|---:|---:|---|
| 2026-08 | asma.k | 2,536 | 43081239.62000005 | 2,536 | 821 | none |
| 2026-08 | rasha.s | 1,971 | 182932600.90000033 | 1,971 | 330 | none |
| 2026-08 | ghofran.b | 2,953 | 74007539.03000009 | 2,953 | 797 | none |
| 2026-08 | aidi.dev | 0 | 0 | 0 | 0 | none |
| 2026-07-27 | asma.k | 104 | 1823710.95 | 104 | 89 | none |
| 2026-07-27 | rasha.s | 133 | 10194437.23 | 133 | 106 | none |
| 2026-07-27 | ghofran.b | 121 | 3094093.67 | 121 | 84 | none |
| 2025-09-01..2026-08-31 | asma.k | 22,143 | 731582810.3815622 | 22,143 | 4,070 | none |
| 2025-09-01..2026-08-31 | rasha.s | 19,481 | 998519852.0215962 | 19,481 | 4,766 | none |
| 2025-09-01..2026-08-31 | ghofran.b | 25,654 | 744118266.8084981 | 25,654 | 5,490 | none |
| 2026-09 | asma.k | 864 | 32133862.43 | 864 | 472 | none |
| 2026-09 | rasha.s | 911 | 18947248.90 | 911 | 547 | none |
| 2026-09 | ghofran.b | 916 | 53779613.49 | 916 | 488 | none |
| 2026-09 | aidi.dev | 43 | 51253.0 | 43 | 3 | none |

- **The rows are identical.** These collectors record no notes between 00:00 and 03:00 Riyadh time, so the Riyadh-vs-UTC difference does not show here. The test suite proves it on synthetic rows (§5).
- **Legacy `totalAmount` is not reproduced.** It repeats each case's debt once per note: rasha.s shows SAR 998.5 M for 4,766 cases. v2 reports note and distinct-case counts instead.

## 5. Tests

`docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_repa api-test -q tests/test_reports_v2_a.py`: **18 passed** (about 4 s).

| Area | Test and what it proves |
|---|---|
| Definitions | `test_definitions_keys_formats_and_filters`: the five keys; formats csv/xlsx/xlsx_ar; the family grouping; RPT-01 protected flags; no sort on a protected field; RPT-02/03 columns and filters; `created` required on RPT-04/05 (max 366 on RPT-04) with Riyadh semantics; `note_selection` on RPT-04 only; the only money column in RPT-04 is `original_debt`, and it is never a total; column roles |
| Permissions | `test_screen_keys_gate_every_report`: SuperAdmin and supervisor see all five; the collector role sees RPT-02 and RPT-05; the target-editor and identity-admin roles see RPT-01; the no-key role gets 403 `required_any` on the definition, rows and export of each report |
| Validation | `test_validation`: required `created`; `range_too_long` at 367 days (366 accepted); `unknown_value`; RPT-05 rejects `recorder`/`note_selection`; single-choice `case_state`; a protected sort is rejected; money precision |
| RPT-01 scope | `test_rpt01_employee_scope`: the supervisor sees exactly the supervised user, the contract-linked user with no case, and the owner of an in-scope case. It does not see the unlinked user or the historical owner/recorder. Identity admin and SuperAdmin see all; a lone key holder sees only themselves; the branch selector follows the scope |
| RPT-01 totals | `test_rpt01_assignment_totals_follow_the_case_scope_and_case_state`: exact debt over the case scope (4 vs 3 cases, 1,350.36 vs 350.35); `case_state` unarchived/archived; sort by case_count then id |
| Protected fields | `test_rpt01_protected_fields_follow_user_privacy`: the own row is never redacted; a supervisor without keys gets identity and targets null; a `users_edit_target` holder sees targets but not identity; `users_edit` sees all; an email is not searchable; **the CSV export redacts exactly like the preview** |
| RPT-02 | `test_rpt02_scope_unarchived_notes_assignment_and_money`: scope (supervised user yes, unlinked contract no, unassigned yes); archived excluded; the latest effective note moves back when a note is retired; `has_note` and `note_status`; assignment age exact against `snapshot_at`, and **null** when the latest assignment went to someone else; money with approved/pending/rejected/reversed and exact totals |
| RPT-02 filters | `test_rpt02_filters_debt_account_and_attributes`: inclusive exact debt bounds; literal `%_\` account; Arabic `q`; lock; provision `125.5` vs null |
| RPT-03 | `test_rpt03_archive_actor_reason_date_restore_and_scope`: actor, reason and date from the current record; no record kept with nulls; exact totals; restore removes the case; re-archive replaces the attribution; actor and reason filters; **Riyadh archive-day bounds** (20:59:59Z / 21:00Z); the actor selector is scoped |
| Riyadh dates | `test_note_dates_are_riyadh_days`: four stored instants around both window ends, for RPT-04 and RPT-05; one-day windows; `note_hour` 0 and 23 |
| RPT-04 | `test_rpt04_scope_retired_filters_and_totals`: recorder-independent case scope; a retired note excluded; totals; recorder, branch, status, reachability, callback, text and case_state filters; a corrected note (effective text, `corrected`, date unchanged); the recorder selector is window- and scope-bound |
| Latest matching note | `test_rpt04_latest_matching_note_across_the_full_filtered_set`: 35 cases × 4 notes (140) with equal-timestamp ties; the filters act **before** the selection (recorder, text); ties go to the highest id; empty results keep full totals; **4 pages of 10 = 1 page of 200**; the display sort does not change the set; a page beyond the end has full totals; the one-day window; **the full CSV export equals the preview** |
| Recorder rule | `test_rpt05_recorder_rule_regardless_of_current_owner`: after reassignment, the recorder's notes stay in RPT-05 (the preview and the worker export) while the case leaves RPT-02; the owner is shown separately; a supervisor's own note outside its scope is in RPT-05 but not RPT-04; notes by others are excluded; no repeated-debt total |
| Paging | `test_rpt02_paging_is_stable_with_full_ties`: **110 fully tied cases × 6 sorts**: 6 pages of 20 = one page of 200, ordered by case id, no loss or duplicate; export = preview |
| Full export | `test_full_exports_equal_previews_rpt03_rpt05`: 23 rows each, every CSV cell equals the preview, totals equal (also RPT-01, RPT-02, RPT-04 and RPT-05 in the tests above) |
| Formula safety | `test_formula_safety_in_notes_and_names`: note text, customer name and contact number in CSV prefixed with `'`; no `<f>` in XLSX |
| Arabic XLSX | `test_arabic_xlsx_rpt01_and_rpt03`: RTL on both sheets; sheet names المستخدمين / عملاء الأرشيف / الملخص; Arabic headers; نشط/غير نشط, لا; money numeric; archive time in Riyadh local; Arabic summary (row count, النشطون, filter label) |
| Legacy vs v2 | `test_documented_differences_from_the_legacy_endpoints`: the real legacy `/comments`, `/comments/current_user`, `/clints/archive/`, `/clints` and `/uesrs/export` on synthetic rows reproduce every category: Riyadh first day, UTC last evening, case scope, archive without a record, identical `/clints`, repeated-debt `totalAmount`, employee scope |

Whole suite (`-e TEST_DB_PREFIX=cawp_test_repa api-test -q tests`): the last complete run with my files gave **751 passed, 9 failed, all 9 in `tests/test_reports_v2_c.py`** (stream R-C). Those tracebacks show `???` source lines, which means R-C's file was being edited during the run. The same suite **without my test file** also had an R-C failure (`test_rpt23…`). R-A with R-C alone: 52 passed. R-A with R-B: 95 passed. See also §8.4.

## 6. Timings on `cawp-db` (474k cases, 1.27M notes; read-only; median of 5)

The preview is page 1 (50 rows) plus the exact totals in one snapshot, measured in-process.

| Preview | Rows | Median ms (min–max) |
|---|---:|---:|
| RPT-01 aidi.dev | 289 | 318 (308–340) |
| RPT-01 aidi.dev, `case_state=unarchived` | 289 | 236 |
| RPT-01 Tamara (collector scope) | 289 | 208 |
| RPT-02 aidi.dev | 171,456 | 446 (420–523) |
| RPT-02 marwan.a / rasha.s | 66,921 / 67,874 | 293 / 300 |
| RPT-02 aidi.dev `q=محمد` | 3,254 | 338 |
| RPT-02 aidi.dev sort `-outstanding_amount` | 171,456 | 529 |
| RPT-02 aidi.dev sort `last_note_at`, page 200 | 171,456 | 754 |
| RPT-03 aidi.dev | 302,501 | 524 |
| RPT-03 marwan.a | 61,433 | 289 |
| RPT-03 aidi.dev, archived in September 2026 | 173,035 | 1,469 |
| RPT-04 aidi.dev, August 2026 | 149,138 | 186 |
| RPT-04 aidi.dev, August, `latest_per_case` | 76,547 | 909 |
| RPT-04 aidi.dev, 365 days | 761,452 | 922 |
| RPT-04 aidi.dev, 365 days, `latest_per_case` | 133,377 | 2,658 |
| RPT-04 marwan.a, August | 31,863 | 244 |
| RPT-04 aidi.dev, August, `text=وعد` | 7,892 | 83 |
| RPT-05 asma.k, 365 days / August | 22,143 / 2,536 | 54 / 17 |

Selectors (aidi.dev), median ms:

| Selector | Median ms | Options |
|---|---:|---:|
| RPT-02 owner | 116 | 95 |
| RPT-02 note_status | 117 | 164 |
| RPT-03 archived_by | 433 | 11 |
| RPT-03 archive_reason | 379 | 3 |
| RPT-04 recorder (August) | 130 | 80 |
| RPT-04 status (August) | 129 | 165 |
| RPT-01 role | 97 | 11 |

Full exports (aidi.dev unless noted; fresh snapshot, batches of 500, `exports.generate`, file written to /tmp and deleted):

| Export | Rows | Time | Rows/s | File |
|---|---:|---:|---:|---:|
| RPT-01 CSV / Arabic XLSX | 289 | 0.57 s / 0.57 s | ~505 | 71 KB / 49 KB |
| RPT-02 CSV / XLSX / Arabic XLSX | 171,456 | 6.2 s / 8.8 s / 8.8 s | 27,762 / 19,571 / 19,408 | 57 MB / 27.7 MB / 27.7 MB |
| RPT-03 CSV / Arabic XLSX | 302,501 | 10.8 s / 18.8 s | 28,022 / 16,057 | 114 MB / 53 MB |
| RPT-04 August CSV / Arabic XLSX | 149,138 | 4.4 s / 8.3 s | 33,825 / 18,074 | 63 MB / 34 MB |
| RPT-04 August `latest_per_case` CSV | 76,547 | 3.1 s | 24,372 | 32 MB |
| RPT-05 asma.k 365 days CSV / Arabic XLSX | 22,143 | 0.64 s / 1.14 s | 34,473 / 19,421 | 10 MB / 4.6 MB |

**No index (and no migration) was needed:**
- The RPT-03 archive-date filter already uses `ix_clintarchive_create`. Its cost is the join over every archived case in scope: 630 ms for the totals in the September window, where 57 % of archived cases were archived.
- RPT-04 `latest_per_case` over a year is dominated by the `DISTINCT ON` sort of 761k window notes, which no index removes. It stays well under the 60 s preview timeout.
- RPT-02/03 compute the assignment age as a scalar subquery on `ix_specify_clint_created`. PostgreSQL evaluates it after the sort and LIMIT: the plan shows `loops=50`, not 171k.
- The RPT-02 page sort (aidi.dev, 171k rows) spills about 150 MB to disk, because the sorted rows are wide (the `attribute` jsonb for product and provision). It still takes 446 ms. The legacy `/clints` avoids this with a "deferred join" (sort the ids, then fetch 50 rows); the engine's `rows_sql` could do the same. That is a framework change, so it is listed in §8.

## 7. Differences from legacy: decisions taken

1. **Scope corrections.** Historical relationships never widen access (spec). Legacy `/clints/archive/` and `/comments` had **no scope at all**. v2 applies the current case scope. For the three مشرف2 supervisors this is 241,066–301,950 fewer archived cases and 79–99 % fewer notes in August 2026. This is the largest visible change in the stream.
2. **Riyadh days for notes** instead of UTC days. Legacy `inclusive_day_range` ran in a UTC session.
3. **Archived cases without an archive record** are kept, with null archive fields. There are 2 on `cawp-db`; legacy's inner join dropped them.
4. **Unknown stays unknown:**
   - assignment age: legacy counted from any latest assignment and showed 0 when there was none;
   - provision: legacy showed 0 when absent.
5. **The RPT-05 repeated-debt `totalAmount` is not reproduced.** Counts replace it.
6. **Exact money:**
   - per-case `round(amount, 2)`; legacy float sums differ by the 340 over-precise amounts;
   - v2 adds approved, pending and outstanding totals;
   - `original_debt` is never summed on the note reports.
7. **The RPT-04 latest-note selection runs on the server, over the full filtered set,** before paging, totals and the export. The legacy browser toggle deduplicated per page, which the rebuild contract flagged.
8. **RPT-01 is gated by `users`.** Legacy `GET /uesrs` is JWT-only, so every account can read the roster; that route is unchanged.

## 8. Open items and notes for the lead

1. **Decision: RPT-01 employee scope.** Identity administrators (`users_details`/`users_edit`) and SuperAdmin see every employee; other `users` holders see linked employees only.
   - On `cawp-db` every `users` holder is an identity administrator, so no current user sees fewer employees than legacy.
   - Confirm, or choose "supervised-user links only" (the rebuild's stricter rule).
2. **Decision: RPT-03/RPT-04 scope correction** (§7.1). This follows the spec's current-scope rule. If supervisors need the old global archive or notes view, it would have to be a separate explicit permission.
3. **Framework notes** (not changed; `core/reports/*` is R0's):
   - **Sort errors are reported separately.** `engine.preview` validates the filters first, so a bad `sort` together with a bad filter reports only the filter error. The framework document says "every bad parameter at once".
   - **Selectors need required filters.** `options.options()` normalizes all of the report's filters, so a selector call on a report with a **required** filter (RPT-04/05 `created`) returns 422 unless the window is sent. I made `created` a `depends_on` of every RPT-04/05 selector, so the frontend sends it anyway; the frontend should know this.
   - **Internal-registry access.** I register my selector entities only if their key is absent (checking `options._ENTITIES`, as R-B does). A public `options.has_entity()` or an idempotent `register_entity` would avoid touching the private dict.
4. **Test fixtures.**
   - R-B and I first both used the UUID prefix `e6e6e6e6-…`. With `ON CONFLICT DO NOTHING`, whichever seed loaded first won, and the whole R-B suite failed. Mine now uses `ea0ea0ea-…`.
   - A convention (one prefix per stream, listed in `tests/conftest.py`) would prevent this.
   - The last whole-suite failures were in R-C's in-progress file (§5).
5. **Performance, if needed later:**
   - RPT-04 `latest_per_case` over a full year takes 2.7 s. A narrower default window in the UI (a month: 0.9 s) is enough for now.
   - A framework-level "deferred join" in `engine.rows_sql` (order and limit the ids, then join the wide columns) would remove the RPT-02/03 on-disk page sort (§6).
6. **Frontend (after the backend):**
   - RPT-04/05 need default `created_from`/`created_to` values; legacy defaulted to a date pair too.
   - `note_selection` is a single-choice enum; send nothing for "all".
   - RPT-01's `case_state` works the same way.
7. **Not mapped** (as in R0): the generic legacy MUI operators and `*_Qkey` filters; the Excel inquiry filter on the case lists (`customeFilterInquiryClintByExcelValue`, `selectedClients_Qkey`), which the Phase 2 spreadsheet inquiry covers; and the legacy JSON attribute-contains filter.
