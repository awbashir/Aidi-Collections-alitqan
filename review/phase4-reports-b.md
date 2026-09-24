# Phase 4, stream R-B: reports RPT-09 to RPT-16

Date: 2026-09-24. Branch `phase-4-reports`, not committed. Scope: stream R-B in `docs/phase4-spec.md`.

**No migration was added and nothing was applied.** 0044 and 0045 are unused (see §8). No container was restarted. Every measurement on `cawp-db` ran read-only:
- the connections used `default_transaction_read_only=on` (`core.reports.perf.make_app`);
- the v2 queries ran in the framework's `REPEATABLE READ READ ONLY` snapshot;
- the only legacy call that went over HTTP was `GET /portfolios` on the running `cawp-api`, which is a read.

## 1. Files

| Area | Files |
|---|---|
| Reports | `CAWP-API/services/reports_v2_b.py`: `REPORTS` = RPT-09 … RPT-16. It is discovered by `core.reports.registry`; no framework file was changed. |
| Tests | `CAWP-API/tests/test_reports_v2_b_support.py`: fixtures and helpers, 1 smoke test.<br>`test_reports_v2_b.py`: 39 tests (definitions, keys, validation, promises).<br>`test_reports_v2_b_cases.py`: 11 tests (RPT-12 … 16).<br>`test_reports_v2_b_exports.py`: 26 tests (paging, export = preview, XLSX, Arabic XLSX, formula safety, worker rules). |
| Fixture | `CAWP-API/tests/fixtures/seed_reports_b.sql`: users `rb.*`, ids `e6e6e6e6-…` |
| Harness | `CAWP-API/tests/test_reports_v2_b_reconcile.py`: reconciliation and timings on `cawp-db`. It is read-only and contains no tests. Run it with `docker exec -w /app -e PYTHONPATH=/app cawp-api python tests/test_reports_v2_b_reconcile.py reconcile\|timings --json out.json`. |
| Evidence (git-ignored) | `logs/repb_reconcile.json` (98 comparisons), `logs/repb_timings.json`, `logs/repb_fullsuite.log` |
| Report | this file |

Not touched:
- `core/reports/*`, `api/*`, `app.py`, `models.py`;
- `core/permissions.py` (every key already exists);
- `conftest.py`, `seed.sql`, the frontend, and other streams' files.

## 2. Reports and keys (screen-key rule)

| Report | Legacy endpoint → screen | Key(s), any-of | Evidence (CAWP-frontend/src unless noted) | Arabic XLSX |
|---|---|---|---|---|
| RPT-09 promises (team/global) | `GET /payment_date` (+ `/export`) → `/report/users/paymentDates` | `report_users_paymentDates` | `routes.config.ts:257`, `navigation.config/index.ts:227`, `permissions/form/Fields.tsx:385` | yes (`exportPaymentDates.tsx`: `rightToLeft`, Arabic headers) |
| RPT-10 my recorded promises | `GET /payment_date/currentUser` (+ `/export`) → `/report/collectors/paymentDates` | `report_collectors_currentUser_paymentDates` | `routes.config.ts:311`, `navigation…:317`, `Fields.tsx:422` | yes (`exportCurrentUserPaymentDates.tsx`) |
| RPT-11 promises on my cases | `GET /payment_date/user` (no screen) | `reports_performance` | the only caller is the unused `apiGetPaymentDateUser` (`services/DashService.ts:33`) | no (no legacy screen) |
| RPT-12 portfolios | `GET /portfolios` (+ `/export`) → `/report/portfolios/portfolios` **and** `/portfolios` | `report_portfolios`, `portfolios` | `routes.config.ts:290` and `:85`; `navigation…:267`, `:450`; `Fields.tsx:406-408`; the legacy export is already `@required_any('portfolios', 'report_portfolios')` (`CAWP-API/app.py:2914`) | yes (`exportPortfolios.tsx`, `exportPortfolis_v2.tsx`) |
| RPT-13 cases with payment records | `GET /clints/have_payments` (+ `/export`) → `/report/clients/active` | `report_clintes_active` | `routes.config.ts:268`, `navigation…:247`, `Fields.tsx:396` | yes (`exportClientActive.tsx`) |
| RPT-14 cases without payment records | `GET /clints/not_have_payments` (+ `/export`) → `/report/clients/inactive` | `report_clintes_inactive` | `routes.config.ts:279`, `navigation…:256`, `Fields.tsx:400` | yes (`exportClientInactive.tsx`) |
| RPT-15 follow-up age | `GET /clints/neglect` (+ `/export`) → `/report/quality/neglect` | `report_quality_neglect` | `routes.config.ts:301`, `navigation…:287`, `Fields.tsx:412-414` | yes (`exportNneglect.tsx`) |
| RPT-16 callbacks on my cases | `GET /clints/currentUser/have_nextCalls` (+ `/export`) → `/report/collectors/nextCalls` | `report_collectors_currentUser_nextCalls` | `routes.config.ts:331`, `navigation…:326`, `Fields.tsx:426` | yes (`exportCurrentUserNextCalls.tsx`) |

Who holds the keys on `cawp-db`:
- the 253 collectors (محصل) and 8 processing users (معالجة) hold RPT-10 and RPT-16 only;
- supervisors (مشرف, مشرف2) hold RPT-09, 12 (`report_portfolios`) and 15, and مشرف also holds 13/14;
- operations, collection manager and ADMIN hold all seven screen keys;
- **only ADMIN holds `reports_performance`** (RPT-11, see §9).

## 3. Definitions

Common to all eight reports:
- money is exact `numeric` (strings in JSON and CSV, `#,##0.00` numbers in XLSX);
- `original_debt` is per-row context and is summed once per case only in the case and portfolio reports;
- the four roles stay separate columns: current owner `owner_*`, recorder `recorder_*`, approval actor (inside the payment status) and canceller `cancelled_by_*`;
- no employee identity, mobile or email column exists; `identity_number` is the customer's.

### RPT-09 / RPT-10 / RPT-11: promises (family `promises`)

- **Source.** `v_promise_state` (Phase 1) joined to `"paymentDates"`, the case, the recorder, the canceller and the case's latest effective note.
- **State:**
  - `cancelled` if cancelled;
  - else `fulfilled` if allocated approved evidence is > 0 and ≥ the amount;
  - else `overdue` if the Riyadh civil due date is before today;
  - else `open`.

  "Promise discussed" never creates a promise. A reversal voids the allocation, and the promise reopens (tested).
- **Inclusion:**
  - **RPT-09:** current case scope; archived cases are included, with a `case_state` filter.
  - **RPT-10:** recorder = me, **regardless of the case's owner or scope** (recorder rule).
  - **RPT-11:** case owner = me now, case unarchived, any recorder. All dates by default.
- **Columns:**
  - promise id, state, due date (civil), promised amount;
  - allocated (approved evidence), remaining (open/overdue only), fulfilled at;
  - `imported_baseline`: imported promise with no allocation, so its fulfilment is unknown;
  - entered at; recorder name, login and branch;
  - case id, account, customer, customer ID, contract, portfolio, original debt;
  - owner name, login and branch; case archived;
  - latest note at and status (legacy "حالة العميل");
  - cancelled at, by and reason.
- **Filters:**
  - `due_date` (inclusive civil dates on the stored instant: `[Riyadh 00:00 of from, Riyadh 00:00 after to)`, so the partial index `ix_paymentdates_payment_date` is usable);
  - `state`; `created` (entered, Riyadh day bounds);
  - `recorder` and `recorder_branch` (RPT-09; RPT-11 has `recorder`);
  - `owner` and `owner_branch` (RPT-09);
  - `contract`, `portfolio`, `case_state` (RPT-09/10);
  - `amount` (exact), `account`, `q`.
- **Sorts:** entered at (default `-created_at`, as legacy), due date, amount, allocated, account, customer, recorder.
- **Totals:**
  - `count`;
  - `promised_amount` (obligation, not recovery), `allocated_amount` (already counted in collections, never added to recovery), `remaining_amount`;
  - `open` / `overdue` / `fulfilled` / `cancelled` counts and amounts;
  - `imported_baseline_count`, `case_count`.

### RPT-12: portfolios (family `portfolios`)

- **One row per visible portfolio, empty ones included:**
  - SuperAdmin sees all;
  - otherwise a portfolio is visible when its contract is supervised or it holds a case owned by a user in the scope. This is exactly "holds a case in the current scope"; equality was checked against an EXISTS form for six actors.
- **Aggregates** cover only the cases in the caller's current case scope, so access to one case never reveals the whole portfolio.
- **`case_state`** (all / unarchived / archived) selects the cases that enter the aggregates. The portfolio stays in the list with zeros.
- **Columns:**
  - id, name, contract, deleted flag;
  - created (legacy "تاريخ الاسناد") and age in days (complete 24 h periods at the snapshot);
  - cases, unarchived, archived; original debt;
  - payment records; recorded, approved, pending, rejected and reversed amounts;
  - outstanding = debt − approved.
- **Filters:** `created` (Riyadh day bounds), `contract`, `portfolio`, `case_state`, `portfolio_state`, `has_cases`, `q` (portfolio/contract name).
- **Sorts:** created (default `-created_at`, as legacy), name, contract, cases, debt, approved, outstanding.
- **Totals:** the column sums plus `empty_portfolio_count`.

### RPT-13 / RPT-14: cases with / without payment records (family `cases`)

- **A payment record** is a `"Payments"` row that is not legacy-deleted, or that was reversed (the reversal is kept as history), in any status. Only approved, non-reversed money reduces `outstanding_amount`.
- **RPT-13:** unarchived cases in scope with ≥ 1 record.
  - Per-status amounts, record count, last payment date (civil).
  - `evidence` filter: has an approved payment / unapproved only.
  - Totals: debt (once per case), records, recorded / approved / pending / rejected / reversed / outstanding, `cases_with_approved`, `cases_unapproved_only`.
- **RPT-14:** unarchived cases in scope with no record, regardless of balance.
  - Totals: debt, cases with a note, cases without a note.
- **Filters (both):** `owner`, `owner_branch`, `contract`, `portfolio`, `assignment`, `note_status` (latest effective note), `account`, `q`.
- **Sorts:** case created (default `-created_at`, as legacy), debt, account, customer, owner, latest note; RPT-13 also records, approved and recorded.

### RPT-15: follow-up age (family `cases`)

- **Rows:** every unarchived case in scope.
- **Latest effective note:** `clints.last_comment_id` / `last_comment_at`, maintained by `db/03` over non-retired notes (a v2 retirement moves it back).
- **`age_days`:** complete 24 h periods between that note and the snapshot's `now()`.
  - **No note = null (unknown), never 0.**
  - A future-dated note gives a negative age, which is not clamped.
- **Default sort:** `last_note_at` ascending, nulls last (legacy "most neglected first").
- **Filters:** the case filters plus `age` buckets (`0_7`, `8_30`, `31_90`, `91_180`, `181_plus`, `none`; sargable on `last_comment_at`) and `last_note` (Riyadh day bounds).
- **Totals:** with / without a note, more than 30 / 90 days, debt.

### RPT-16: callbacks on my cases (family `callbacks`)

- **Rows:** one per non-retired note with a `next_contact_date`, on a case I own now and that is unarchived. The note's author may be anyone.

  This is the legacy paged "next calls" semantics: one row per callback comment, and a plain note never closes a callback. The legacy export looked at the latest comment only; v2 uses the paged rule for both preview and export.
- **Phase 1 follow-ups:** each row carries the status of the follow-up that the note created (`follow_ups.source_comment_id`):
  - `open` (due today or later) or `late` (open, civil due date before Riyadh today, the worklist rule);
  - `completed` (with `completed_late`), `superseded` or `cancelled`;
  - `untracked`: no follow-up, e.g. older callbacks of a case before the Phase 1 backfill.

  The row also carries the case's current open follow-up (`current_follow_up_at`) and the latest assignment time (legacy export "ايام الاسناد").
- **Filters:** `callback` (Riyadh day bounds on `next_contact_date`), `follow_up_status`, `note_created`, `recorder`, `note_status`, `contract`, `portfolio`, `q` (including the note text).
- **Default sort:** `-callback_at`.
- **Totals:** `count` (callback notes), `case_count`, and one count per follow-up status. The debt is never summed; legacy repeated it per row.

## 4. Decisions applied

- **Recorder rule.**
  - RPT-10 is the caller's recorded promises, with no case-scope predicate (like RPT-07 after the lead's decision).
  - RPT-11 and RPT-16 are "cases I own now, unarchived", whoever recorded.
  - RPT-09, 12, 13, 14 and 15 use the current case scope.
  - The export worker re-applies each rule at generation: a case reassigned after the request leaves RPT-11/16 but stays in RPT-10 (tested).
- **Screen-key rule.** §2. RPT-12 is any-of the two screens that showed and exported portfolio totals; the legacy export already uses exactly that pair.
- **Promises.** `v_promise_state` only; fulfilment is allocated approved evidence (§3).
- **Callbacks.** Phase 1 follow-ups plus callback notes (§3). A historical callback row is not a dialing instruction.
- **Dates.** Due dates are inclusive civil dates; note, callback and portfolio-creation filters use Riyadh day bounds. The half-open UTC range is never `<= 23:59:59`.

## 5. Reconciliation with the legacy endpoints on `cawp-db`

**Method** (`tests/test_reports_v2_b_reconcile.py reconcile`):
1. For each actor and window, the **real legacy handler** runs with a JWT for the actor: `api/reports.py` and `api/clients_reports.py` in-process, and `GET /portfolios` over HTTP on `cawp-api`.
2. An SQL replica rebuilds the legacy row set, and **its count must equal the endpoint's `countRow` or list length.**
3. The v2 report runs for the same actor and window, and every differing row is put in a named category.

**Result:**
- **98 comparisons; the replica matched the endpoint in every one; category `other` = 0.**
- For the promise reports, legacy exact Σ − legacy-only rows + v2-only rows = v2 `promised_amount`, to the halala.

Categories:

| Code | Meaning |
|---|---|
| `−scope (legacy unscoped)` | **Scope correction.** Legacy `/payment_date` has no scope at all: every JWT user sees every promise. v2 RPT-09 keeps only promises on cases in the caller's current case scope. |
| `−deleted promise` | Legacy `/payment_date/user` does not filter `is_deleted`; v2 excludes deleted promises. There are 0 on `cawp-db`; tested on synthetic rows. |
| `−deleted payment row` | Legacy `/clints/have_payments` counts every `"Payments"` row, deleted ones included; v2 counts records that are live or reversed. There are 0 legacy-deleted, non-reversed payments on `cawp-db`; tested. |
| `−retired note` | Legacy next-calls does not filter `is_deleted`; v2 excludes retired notes. Tested. `cawp-db` has 1 retired note, and it has no callback. |
| `±Riyadh day` | **Inclusive Riyadh days.** Legacy compares `date(next_contact_date)` in the UTC session; v2 uses Riyadh day bounds. 6 live callbacks on `cawp-db` fall on different civil dates. |
| money rounding | Legacy sums raw `float` amounts. v2 rounds each amount to the halala first, then sums exactly in `numeric`. 340 case amounts have more than two decimals. |

### 5.1 Promises: RPT-09 vs `/payment_date`, RPT-10 vs `/payment_date/currentUser`, RPT-11 vs `/payment_date/user`

| Report | Legacy endpoint | Actor | Window (due date) | Legacy rows | Legacy Σ promised (exact) | Legacy `totalAmount` | v2 rows | v2 promised | v2 open / overdue / fulfilled / cancelled | Differences (rows, SAR) |
|---|---|---|---|---:|---:|---:|---:|---:|---|---|
| RPT-09 | `/payment_date` | aidi.dev | 2025-01-01..2025-12-31 | 895 | 7,018,644.04 | – | 895 | 7,018,644.04 | 0 / 895 / 0 / 0 | none |
| RPT-09 | `/payment_date` | maher.j | 2025-01-01..2025-12-31 | 895 | 7,018,644.04 | – | 895 | 7,018,644.04 | 0 / 895 / 0 / 0 | none |
| RPT-09 | `/payment_date` | bader.b | 2025-01-01..2025-12-31 | 895 | 7,018,644.04 | – | 4 | 40,637.97 | 0 / 4 / 0 / 0 | −scope (legacy unscoped) 891 (6,978,006.07) |
| RPT-09 | `/payment_date` | marwan.a | 2025-01-01..2025-12-31 | 895 | 7,018,644.04 | – | 454 | 1,149,807.81 | 0 / 454 / 0 / 0 | −scope (legacy unscoped) 441 (5,868,836.23) |
| RPT-09 | `/payment_date` | qasim.s | 2025-01-01..2025-12-31 | 895 | 7,018,644.04 | – | 0 | 0.00 | 0 / 0 / 0 / 0 | −scope (legacy unscoped) 895 (7,018,644.04) |
| RPT-10 | `/payment_date/currentUser` | rasha.s | 2025-01-01..2025-12-31 | 0 | 0.00 | 0 | 0 | 0.00 | 0 / 0 / 0 / 0 | none |
| RPT-10 | `/payment_date/currentUser` | omnia.m | 2025-01-01..2025-12-31 | 158 | 595,856.92 | 2,917,381.05 | 158 | 595,856.92 | 0 / 158 / 0 / 0 | none |
| RPT-10 | `/payment_date/currentUser` | ghofran.b | 2025-01-01..2025-12-31 | 0 | 0.00 | 0 | 0 | 0.00 | 0 / 0 / 0 / 0 | none |
| RPT-10 | `/payment_date/currentUser` | marwan.a | 2025-01-01..2025-12-31 | 0 | 0.00 | 0 | 0 | 0.00 | 0 / 0 / 0 / 0 | none |
| RPT-10 | `/payment_date/currentUser` | aidi.dev | 2025-01-01..2025-12-31 | 0 | 0.00 | 0 | 0 | 0.00 | 0 / 0 / 0 / 0 | none |
| RPT-09 | `/payment_date` | aidi.dev | 2025-09-01..2026-08-31 | 9,339 | 48,448,872.35 | – | 9,339 | 48,448,872.35 | 0 / 9,339 / 0 / 0 | none |
| RPT-09 | `/payment_date` | maher.j | 2025-09-01..2026-08-31 | 9,339 | 48,448,872.35 | – | 9,338 | 48,447,872.35 | 0 / 9,338 / 0 / 0 | −scope (legacy unscoped) 1 (1,000.00) |
| RPT-09 | `/payment_date` | bader.b | 2025-09-01..2026-08-31 | 9,339 | 48,448,872.35 | – | 410 | 12,519,054.50 | 0 / 410 / 0 / 0 | −scope (legacy unscoped) 8,929 (35,929,817.85) |
| RPT-09 | `/payment_date` | marwan.a | 2025-09-01..2026-08-31 | 9,339 | 48,448,872.35 | – | 6,194 | 23,427,330.37 | 0 / 6,194 / 0 / 0 | −scope (legacy unscoped) 3,145 (25,021,541.98) |
| RPT-09 | `/payment_date` | qasim.s | 2025-09-01..2026-08-31 | 9,339 | 48,448,872.35 | – | 24 | 1,056,890.17 | 0 / 24 / 0 / 0 | −scope (legacy unscoped) 9,315 (47,391,982.18) |
| RPT-10 | `/payment_date/currentUser` | rasha.s | 2025-09-01..2026-08-31 | 1,064 | 3,236,645.45 | 40,481,874.82 | 1,064 | 3,236,645.45 | 0 / 1,064 / 0 / 0 | none |
| RPT-10 | `/payment_date/currentUser` | omnia.m | 2025-09-01..2026-08-31 | 901 | 4,768,460.64 | 60,654,718.09 | 901 | 4,768,460.64 | 0 / 901 / 0 / 0 | none |
| RPT-10 | `/payment_date/currentUser` | ghofran.b | 2025-09-01..2026-08-31 | 449 | 2,747,936.91 | 10,318,117.63 | 449 | 2,747,936.91 | 0 / 449 / 0 / 0 | none |
| RPT-10 | `/payment_date/currentUser` | marwan.a | 2025-09-01..2026-08-31 | 18 | 160,439.28 | 1,092,724.77 | 18 | 160,439.28 | 0 / 18 / 0 / 0 | none |
| RPT-10 | `/payment_date/currentUser` | aidi.dev | 2025-09-01..2026-08-31 | 0 | 0.00 | 0 | 0 | 0.00 | 0 / 0 / 0 / 0 | none |
| RPT-09 | `/payment_date` | aidi.dev | 2026-09-01..2026-09-30 | 2,243 | 6,008,113.82 | – | 2,243 | 6,008,113.82 | 672 / 1,570 / 0 / 1 | none |
| RPT-09 | `/payment_date` | maher.j | 2026-09-01..2026-09-30 | 2,243 | 6,008,113.82 | – | 2,243 | 6,008,113.82 | 672 / 1,570 / 0 / 1 | none |
| RPT-09 | `/payment_date` | bader.b | 2026-09-01..2026-09-30 | 2,243 | 6,008,113.82 | – | 55 | 365,250.00 | 3 / 52 / 0 / 0 | −scope (legacy unscoped) 2,188 (5,642,863.82) |
| RPT-09 | `/payment_date` | marwan.a | 2026-09-01..2026-09-30 | 2,243 | 6,008,113.82 | – | 623 | 3,049,436.58 | 145 / 478 / 0 / 0 | −scope (legacy unscoped) 1,620 (2,958,677.24) |
| RPT-09 | `/payment_date` | qasim.s | 2026-09-01..2026-09-30 | 2,243 | 6,008,113.82 | – | 0 | 0.00 | 0 / 0 / 0 / 0 | −scope (legacy unscoped) 2,243 (6,008,113.82) |
| RPT-10 | `/payment_date/currentUser` | rasha.s | 2026-09-01..2026-09-30 | 80 | 262,439.38 | 1,733,315.62 | 80 | 262,439.38 | 13 / 67 / 0 / 0 | none |
| RPT-10 | `/payment_date/currentUser` | omnia.m | 2026-09-01..2026-09-30 | 47 | 436,452.97 | 4,577,412.10 | 47 | 436,452.97 | 18 / 29 / 0 / 0 | none |
| RPT-10 | `/payment_date/currentUser` | ghofran.b | 2026-09-01..2026-09-30 | 23 | 187,015.16 | 1,725,405.56 | 23 | 187,015.16 | 4 / 19 / 0 / 0 | none |
| RPT-10 | `/payment_date/currentUser` | marwan.a | 2026-09-01..2026-09-30 | 3 | 9,282.88 | 33,580.10 | 3 | 9,282.88 | 0 / 3 / 0 / 0 | none |
| RPT-10 | `/payment_date/currentUser` | aidi.dev | 2026-09-01..2026-09-30 | 11 | 1,705.00 | 12,500.50 | 11 | 1,705.00 | 10 / 0 / 0 / 1 | none |
| RPT-09 | `/payment_date` | aidi.dev | 2026-09-24..2026-09-24 | 55 | 165,627.01 | – | 55 | 165,627.01 | 55 / 0 / 0 / 0 | none |
| RPT-09 | `/payment_date` | maher.j | 2026-09-24..2026-09-24 | 55 | 165,627.01 | – | 55 | 165,627.01 | 55 / 0 / 0 / 0 | none |
| RPT-09 | `/payment_date` | bader.b | 2026-09-24..2026-09-24 | 55 | 165,627.01 | – | 0 | 0.00 | 0 / 0 / 0 / 0 | −scope (legacy unscoped) 55 (165,627.01) |
| RPT-09 | `/payment_date` | marwan.a | 2026-09-24..2026-09-24 | 55 | 165,627.01 | – | 12 | 45,523.80 | 12 / 0 / 0 / 0 | −scope (legacy unscoped) 43 (120,103.21) |
| RPT-09 | `/payment_date` | qasim.s | 2026-09-24..2026-09-24 | 55 | 165,627.01 | – | 0 | 0.00 | 0 / 0 / 0 / 0 | −scope (legacy unscoped) 55 (165,627.01) |
| RPT-10 | `/payment_date/currentUser` | rasha.s | 2026-09-24..2026-09-24 | 2 | 6,000.00 | 41,742.30 | 2 | 6,000.00 | 2 / 0 / 0 / 0 | none |
| RPT-10 | `/payment_date/currentUser` | omnia.m | 2026-09-24..2026-09-24 | 0 | 0.00 | 0 | 0 | 0.00 | 0 / 0 / 0 / 0 | none |
| RPT-10 | `/payment_date/currentUser` | ghofran.b | 2026-09-24..2026-09-24 | 2 | 17,134.70 | 79,778.57 | 2 | 17,134.70 | 2 / 0 / 0 / 0 | none |
| RPT-10 | `/payment_date/currentUser` | marwan.a | 2026-09-24..2026-09-24 | 0 | 0.00 | 0 | 0 | 0.00 | 0 / 0 / 0 / 0 | none |
| RPT-10 | `/payment_date/currentUser` | aidi.dev | 2026-09-24..2026-09-24 | 0 | 0.00 | 0 | 0 | 0.00 | 0 / 0 / 0 / 0 | none |
| RPT-11 | `/payment_date/user` | rasha.s | all dates | 76 | 249,092.20 | – | 76 | 249,092.20 | 11 / 65 / 0 / 0 | none |
| RPT-11 | `/payment_date/user` | omnia.m | all dates | 72 | 722,175.68 | – | 72 | 722,175.68 | 26 / 46 / 0 / 0 | none |
| RPT-11 | `/payment_date/user` | ghofran.b | all dates | 25 | 188,611.49 | – | 25 | 188,611.49 | 4 / 21 / 0 / 0 | none |
| RPT-11 | `/payment_date/user` | marwan.a | all dates | 0 | 0.00 | – | 0 | 0.00 | 0 / 0 / 0 / 0 | none |
| RPT-11 | `/payment_date/user` | aidi.dev | all dates | 15 | 2,216.50 | – | 15 | 2,216.50 | 13 / 0 / 1 / 1 | none |

Reading:
- **Row sets.**
  - Every promise is stored at 00:00Z, so the legacy UTC-day filter and the v2 civil-date filter select the same rows. No date difference occurs.
  - aidi.dev (supervisor of all 15 contracts) matches exactly in every window.
  - maher.j (12 contracts) loses one promise on an unsupervised contract.
- **Totals.**
  - Legacy `/payment_date` returns no total.
  - Legacy `/payment_date/currentUser`'s `totalAmount` is the **sum of the cases' original debts** (e.g. rasha.s, one year: 40,481,874.82 in float), not the promised amount (3,236,645.45).
  - v2 reports `promised_amount` and splits it by state.
- **States on real data.**
  - 11,839 live promises: 737 open, 11,100 overdue, 1 fulfilled, 1 cancelled.
  - 11,824 are the imported baseline: fulfilment unknown, no allocation (`imported_baseline_count`).
  - The other 15 were recorded after Phase 1, by earlier test runs as aidi.dev.
- **RPT-10 and RPT-11** equal legacy exactly for every actor. RPT-10 needs no scope correction under the recorder rule. There are no deleted promises.

### 5.2 RPT-12 vs `/portfolios`

The legacy endpoint is unscoped: every user gets all 173 portfolios and global totals. `customeFilterArchivingStatusesValue[]` = all, `false` or `true` matches v2 `case_state` = all, `active` or `archived`.

| Actor | Cases counted | Portfolios legacy / v2 | Cases legacy / v2 | Legacy debt (float) | v2 debt | Legacy payments (float) | v2 recorded | v2 approved | v2 pending / rejected / reversed | v2 outstanding |
|---|---|---|---|---:|---:|---:|---:|---:|---|---:|
| aidi.dev | all | 173 / 173 | 473,957 / 473,957 | 2,762,173,345.54 | 2,762,173,345.73 | 128,125,553.42 | 128,125,553.42 | 124,375,487.74 | 529,732.34 / 3,216,353.34 / 3,980.00 | 2,637,797,857.99 |
| aidi.dev | unarchived | 173 / 173 | 171,456 / 171,456 | 696,937,122.44 | 696,937,122.63 | 19,608,462.56 | 19,608,462.56 | 18,856,921.09 | 112,193.43 / 635,368.04 / 3,980.00 | 678,080,201.54 |
| aidi.dev | archived | 173 / 173 | 302,501 / 302,501 | 2,065,236,223.10 | 2,065,236,223.10 | 108,517,090.86 | 108,517,090.86 | 105,518,566.65 | 417,538.91 / 2,580,985.30 / 0.00 | 1,959,717,656.45 |
| maher.j | all | 173 / 170 | 473,957 / 446,082 | 2,762,173,345.54 | 2,755,778,231.50 | 128,125,553.42 | 128,123,553.42 | 124,373,487.74 | 529,732.34 / 3,216,353.34 / 3,980.00 | 2,631,404,743.76 |
| maher.j | unarchived | 173 / 170 | 171,456 / 143,581 | 696,937,122.44 | 690,542,008.40 | 19,608,462.56 | 19,606,462.56 | 18,854,921.09 | 112,193.43 / 635,368.04 / 3,980.00 | 671,687,087.31 |
| maher.j | archived | 173 / 170 | 302,501 / 302,501 | 2,065,236,223.10 | 2,065,236,223.10 | 108,517,090.86 | 108,517,090.86 | 105,518,566.65 | 417,538.91 / 2,580,985.30 / 0.00 | 1,959,717,656.45 |
| bader.b | all | 173 / 71 | 473,957 / 12,976 | 2,762,173,345.54 | 665,663,323.07 | 128,125,553.42 | 37,042,357.01 | 35,390,074.11 | 132,400.63 / 1,519,882.27 / 0.00 | 630,273,248.96 |
| bader.b | unarchived | 173 / 71 | 171,456 / 3,180 | 696,937,122.44 | 138,379,761.78 | 19,608,462.56 | 4,358,712.08 | 4,122,016.92 | 54,400.35 / 182,294.81 / 0.00 | 134,257,744.86 |
| bader.b | archived | 173 / 71 | 302,501 / 9,796 | 2,065,236,223.10 | 527,283,561.29 | 108,517,090.86 | 32,683,644.93 | 31,268,057.19 | 78,000.28 / 1,337,587.46 / 0.00 | 496,015,504.10 |
| qasim.s | all | 173 / 53 | 473,957 / 945 | 2,762,173,345.54 | 50,689,437.71 | 128,125,553.42 | 3,453,507.93 | 3,367,502.25 | 13,335.68 / 72,670.00 / 0.00 | 47,321,935.46 |
| qasim.s | unarchived | 173 / 53 | 171,456 / 395 | 696,937,122.44 | 19,519,444.53 | 19,608,462.56 | 967,637.13 | 915,539.13 | 0.00 / 52,098.00 / 0.00 | 18,603,905.40 |
| qasim.s | archived | 173 / 53 | 302,501 / 550 | 2,065,236,223.10 | 31,169,993.18 | 108,517,090.86 | 2,485,870.80 | 2,451,963.12 | 13,335.68 / 20,572.00 / 0.00 | 28,718,030.06 |
| rasha.s | all | 173 / 74 | 473,957 / 133,468 | 2,762,173,345.54 | 1,248,280,944.79 | 128,125,553.42 | 21,340,871.30 | 20,960,793.64 | 149,618.14 / 230,459.52 / 0.00 | 1,227,320,151.15 |
| rasha.s | unarchived | 173 / 74 | 171,456 / 67,874 | 696,937,122.44 | 291,515,290.48 | 19,608,462.56 | 1,175,555.72 | 1,123,533.38 | 9,198.69 / 42,823.65 / 0.00 | 290,391,757.10 |
| rasha.s | archived | 173 / 74 | 302,501 / 65,594 | 2,065,236,223.10 | 956,765,654.31 | 108,517,090.86 | 20,165,315.58 | 19,837,260.26 | 140,419.45 / 187,635.87 / 0.00 | 936,928,394.05 |

Reading:
- **aidi.dev** (all contracts) has identical portfolio and case counts in all three modes.
  - **Payments:** v2 `recorded_amount` (every live or reversed record, any status) equals the legacy `payments` sum exactly, 128,125,553.42. The raw sum is 128,125,553.423; no legacy-deleted, non-reversed payment exists.
  - **Debt:** v2 = 2,762,173,345.73 vs legacy float 2,762,173,345.54 (exact raw sum 2,762,173,345.535779). The +0.19 comes from rounding each of the 340 over-precise case amounts before summing. On a per-portfolio basis, 2 portfolios differ, both by rounding only.
- **Other actors: scope correction.**
  - v2 lists only the portfolios in their scope (maher.j 170, bader.b 71, qasim.s 53, rasha.s 74), and the counts and money cover only their cases.
  - Legacy showed every user the whole book.
  - With the same scope, v2 per-portfolio case counts equal a direct count, so every case difference is a case outside the actor's scope.
- **Money semantics.** Legacy "المبلغ المحصل" summed payments of **all** statuses, and "المتبقي" = debt − that sum. v2 names it `recorded_amount` (not recovery), shows approved / pending / rejected / reversed separately, and `outstanding_amount` = debt − approved. On aidi.dev "all", legacy's remaining (float, 2,634,047,792.12) is 3,750,065.87 below v2's outstanding (2,637,797,857.99). That gap is:
  - the pending, rejected and reversed records (3,750,065.68);
  - plus the 0.19 of debt rounding.

### 5.3 RPT-13 / RPT-14 / RPT-15 vs `/clints/have_payments`, `/clints/not_have_payments`, `/clints/neglect`

These legacy routes already apply the current case scope (`services/clients_scope.scope_condition`, the same semantics as the framework's scope). So the rows are **identical** for all six actors.

| Report | Legacy endpoint | Actor | Legacy rows | Legacy `totalAmount` (float) | v2 rows | v2 debt (exact) | More v2 totals | Differences |
|---|---|---|---:|---:|---:|---:|---|---|
| RPT-13 | `/clints/have_payments` | aidi.dev | 6,968 | 99,250,860.416400 | 6,968 | 99,250,860.43 | 8,703 records; recorded 19,608,462.56; approved 18,856,921.09; 69 cases unapproved-only | none |
| RPT-13 | `/clints/have_payments` | maher.j | 6,967 | 98,943,668.126400 | 6,967 | 98,943,668.14 | 8,702 records; recorded 19,606,462.56; approved 18,854,921.09; 69 cases unapproved-only | none |
| RPT-13 | `/clints/have_payments` | bader.b | 514 | 25,272,044.170000 | 514 | 25,272,044.17 | 837 records; recorded 4,358,712.08; approved 4,122,016.92; 2 cases unapproved-only | none |
| RPT-13 | `/clints/have_payments` | marwan.a | 655 | 16,446,382.640000 | 655 | 16,446,382.64 | 660 records; recorded 1,803,892.02; approved 1,791,407.43; 28 cases unapproved-only | none |
| RPT-13 | `/clints/have_payments` | qasim.s | 78 | 4,955,536.400000 | 78 | 4,955,536.40 | 131 records; recorded 967,637.13; approved 915,539.13; 1 cases unapproved-only | none |
| RPT-13 | `/clints/have_payments` | rasha.s | 504 | 33,698,667.970000 | 504 | 33,698,667.97 | 531 records; recorded 1,175,555.72; approved 1,123,533.38; 27 cases unapproved-only | none |
| RPT-14 | `/clints/not_have_payments` | aidi.dev | 164,488 | 597,686,262.019511 | 164,488 | 597,686,262.20 | – | none |
| RPT-14 | `/clints/not_have_payments` | maher.j | 136,614 | 591,598,340.079512 | 136,614 | 591,598,340.26 | – | none |
| RPT-14 | `/clints/not_have_payments` | bader.b | 2,666 | 113,107,717.610000 | 2,666 | 113,107,717.61 | – | none |
| RPT-14 | `/clints/not_have_payments` | marwan.a | 66,266 | 64,551,448.489999 | 66,266 | 64,551,448.49 | – | none |
| RPT-14 | `/clints/not_have_payments` | qasim.s | 317 | 14,563,908.130000 | 317 | 14,563,908.13 | – | none |
| RPT-14 | `/clints/not_have_payments` | rasha.s | 67,370 | 257,816,622.509999 | 67,370 | 257,816,622.51 | – | none |
| RPT-15 | `/clints/neglect` | aidi.dev | 171,456 | 696,937,122.435904 | 171,456 | 696,937,122.63 | no note (null age): legacy 97,128, v2 97,128 | none |
| RPT-15 | `/clints/neglect` | maher.j | 143,581 | 690,542,008.205902 | 143,581 | 690,542,008.40 | no note (null age): legacy 69,255, v2 69,255 | none |
| RPT-15 | `/clints/neglect` | bader.b | 3,180 | 138,379,761.780000 | 3,180 | 138,379,761.78 | no note (null age): legacy 20, v2 20 | none |
| RPT-15 | `/clints/neglect` | marwan.a | 66,921 | 80,997,831.130000 | 66,921 | 80,997,831.13 | no note (null age): legacy 48,315, v2 48,315 | none |
| RPT-15 | `/clints/neglect` | qasim.s | 395 | 19,519,444.530000 | 395 | 19,519,444.53 | no note (null age): legacy 20, v2 20 | none |
| RPT-15 | `/clints/neglect` | rasha.s | 67,874 | 291,515,290.479999 | 67,874 | 291,515,290.48 | no note (null age): legacy 48,310, v2 48,310 | none |

Reading:
- **Rows:** no difference, because there are no legacy-deleted payment rows.
- **Totals:** legacy `totalAmount` is a float sum of raw debts (e.g. 99,250,860.4164); v2 `original_debt` is the exact sum of halala-rounded debts (99,250,860.43).
- **RPT-15:** the cases with no note equal legacy's null `neglect_days`. v2 keeps them null (unknown), not 0, and sorts them last.

### 5.4 RPT-16 vs `/clints/currentUser/have_nextCalls` (paged)

| Actor | Window (callback date) | Legacy rows | Legacy `totalAmount` (debt × rows) | v2 rows | v2 cases | v2 open / late / completed / superseded / untracked | Differences |
|---|---|---:|---:|---:|---:|---|---|
| rasha.s | 2026-09-01..2026-09-30 | 128 | 2,184,215.33 | 128 | 116 | 17 / 99 / 0 / 0 / 12 | none |
| omnia.m | 2026-09-01..2026-09-30 | 26 | 13,783,696.49 | 26 | 25 | 2 / 23 / 0 / 0 / 1 | none |
| ghofran.b | 2026-09-01..2026-09-30 | 36 | 2,296,446.33 | 36 | 34 | 11 / 23 / 0 / 0 / 2 | none |
| marwan.a | 2026-09-01..2026-09-30 | 0 | 0.00 | 0 | 0 | 0 / 0 / 0 / 0 / 0 | none |
| aidi.dev | 2026-09-01..2026-09-30 | 35 | 36,500.50 | 35 | 2 | 2 / 0 / 13 / 20 / 0 | none |
| rasha.s | 2026-08-01..2026-08-31 | 0 | 0.00 | 0 | 0 | 0 / 0 / 0 / 0 / 0 | none |
| omnia.m | 2026-08-01..2026-08-31 | 1 | 675.02 | 1 | 1 | 0 / 1 / 0 / 0 / 0 | none |
| ghofran.b | 2026-08-01..2026-08-31 | 0 | 0.00 | 0 | 0 | 0 / 0 / 0 / 0 / 0 | none |
| marwan.a | 2026-08-01..2026-08-31 | 0 | 0.00 | 0 | 0 | 0 / 0 / 0 / 0 / 0 | none |
| aidi.dev | 2026-08-01..2026-08-31 | 0 | 0.00 | 0 | 0 | 0 / 0 / 0 / 0 / 0 | none |
| rasha.s | 2025-09-01..2026-09-30 | 128 | 2,184,215.33 | 128 | 116 | 17 / 99 / 0 / 0 / 12 | none |
| omnia.m | 2025-09-01..2026-09-30 | 27 | 13,784,371.51 | 27 | 26 | 2 / 24 / 0 / 0 / 1 | none |
| ghofran.b | 2025-09-01..2026-09-30 | 36 | 2,296,446.33 | 36 | 34 | 11 / 23 / 0 / 0 / 2 | none |
| marwan.a | 2025-09-01..2026-09-30 | 0 | 0.00 | 0 | 0 | 0 / 0 / 0 / 0 / 0 | none |
| aidi.dev | 2025-09-01..2026-09-30 | 35 | 36,500.50 | 35 | 2 | 2 / 0 / 13 / 20 / 0 | none |
| tabby.1 | 2026-08-17..2026-08-17 | 5 | 831.33 | 3 | 3 | 0 / 3 / 0 / 0 / 0 | −Riyadh day 2 |
| tabby.1 | 2026-08-18..2026-08-18 | 5 | 5,895.95 | 7 | 7 | 0 / 6 / 0 / 0 / 1 | +Riyadh day 2 |

All owners at once, comparing the legacy and v2 rules at replica level:

| Window | Legacy rule | v2 rule | − retired | − Riyadh day | + Riyadh day | Balanced |
|---|---:|---:|---:|---:|---:|---|
| 2026-09-01..2026-09-30 | 1,550 | 1,550 | 0 | 0 | 0 | True |
| 2026-08-01..2026-08-31 | 1,190 | 1,190 | 0 | 0 | 0 | True |
| 2025-09-01..2026-09-30 | 3,766 | 3,766 | 0 | 0 | 0 | True |

Reading:
- **Rows** equal legacy in every window, except at the Riyadh day boundary.
  - tabby.1 has two callbacks at 22:51Z on 17 August 2026 (01:51 on 18 August in Riyadh).
  - Legacy lists them on the 17th; v2 lists them on the 18th.
  - The 6 such callbacks on `cawp-db` only matter when a window edge falls between their UTC and Riyadh dates, which is why the three month/year windows show 0.
- **`totalAmount`:** legacy repeats the case debt on every callback row (omnia.m: 13.78 M over 26 rows on 25 cases). v2 reports counts only.
- **Follow-up status on real data:**
  - rasha.s: 17 open, 99 late, 12 untracked.
  - The untracked rows are older callbacks of cases whose backfilled follow-up came from a newer callback note.

## 6. Tests

`docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_repb api-test tests/test_reports_v2_b*.py`

- **Stream R-B: 77 tests, all pass**: support 1, definitions/promises 39, cases 11, exports 26.
- **Together with R0's 56 report tests: 133 pass.**
- **Whole suite (`TEST_DB_PREFIX=cawp_test_repb`): 761 passed, 9 failed.** All 9 failures are in stream R-C's in-progress files (`test_reports_v2_c.py`, `test_reports_v2_c_cdr.py`: range limits, CSV column order), not in R0, R-A or R-B.
- A first full run showed a seed clash: my users were named `b.*`, which Phase 2's `seed_bulk.sql` already uses. They are now `rb.*`, with ids `e6e6e6e6-…`.

| Area | Tests |
|---|---|
| Definitions | all 8 via `GET /v2/reports/<id>`: keys, formats (`xlsx_ar` except RPT-11), Arabic labels, money flags, filter semantics (`inclusive_civil_dates` / `riyadh_day_bounds`), hidden subject columns never returned |
| Screen keys | the list per user (SuperAdmin and supervisor 8; collector RPT-10/11/16; the `portfolios`-only role RPT-12; none 0); 10 parametrized 403s on definition, rows, export and selector; a revoked key takes effect |
| Protected fields | no protected, mobile, email or target column; `identity_number` is the customer's; the recorder's mobile, email and ID never appear in rows |
| Validation | 10 cases: every bad parameter at once (`reversed_range`, `unknown_value`, `invalid`, `unknown_parameter` for RPT-10's recorder and RPT-11's owner, `too_many`, `too_precise`) |
| Promise dates | 6 instants around both ends (20:59:59Z, 21:00Z, 00:00Z) on all three reports: inclusive civil range, one day, one-sided; the entered-at filter on Riyadh day bounds |
| Promise states and totals | open, due today (still open), overdue, fulfilled (auto-allocated approved payment), partial, cancelled (with canceller and reason), imported baseline, deleted (excluded), pending payment (no evidence): exact totals for every state; the state filter; RPT-10/11 totals; a reversal voids the allocation and reopens the promise |
| Recorder rule and scope | RPT-10 keeps my recorded rows on other owners' and out-of-scope cases; RPT-11 follows the current owner; RPT-09 uses contract OR supervised users; filters and selectors never widen; reassignment moves RPT-11 and widens RPT-09; archived cases (RPT-11 excludes them, RPT-09 flags and filters them) |
| Latest note | the status of the latest effective note; retiring it falls back to the previous one |
| RPT-12 | exact counts and money (3-decimal debt rounded per case, every status, reversed, legacy-deleted excluded); the empty portfolio has exact zeros; `case_state` keeps the portfolio; `has_cases`; Arabic `q`; the pushed-down portfolio filter; visibility for SuperAdmin, supervisor (empty supervised portfolio, supervised user's cases only), collectors and the `portfolios`-only role; the selector scope; Riyadh creation days |
| RPT-13/14 | pending-only, rejected-only and reversed-only cases are records; a legacy-deleted-only case is not; archived excluded; exact per-status totals and outstanding; the evidence filter; a new record moves the case between reports; owner, branch, assignment, contract and Arabic `q` filters; owner selector scope |
| RPT-15 | age 10 / 0 / −2 / null; order oldest first with nulls last; retirement falls back (age 40); bucket edges at 7 d 23:55 and 8 d 00:05; the `none` bucket; the descending age sort; Riyadh day bounds on the latest-note date |
| RPT-16 | one row per callback note, other authors included; plain, retired, other-owner and archived excluded; superseded, open, late and untracked statuses and totals; current follow-up; reassignment; Riyadh day bounds at 20:59:59Z / 21:00Z |
| Legacy on synthetic rows | `/payment_date` unscoped; `/payment_date/currentUser` `totalAmount` = debt; `/payment_date/user` lists deleted promises; `/clints/have_payments` counts a deleted row; `/clints/neglect` counts and float total; next-calls: UTC day, retired note, debt × rows |
| **Stable paging** | all 8 reports, 25–60 fully tied rows: every sort in both directions, pages of 20 = one page of 200, no loss or duplicate, ties ordered by id in the sort direction; an empty page past the end keeps the totals |
| **Export = preview** | all 8 reports, CSV: header = columns, every cell equals the full preview, totals equal |
| XLSX and Arabic XLSX | typed values (civil date, `#,##0.00` money, Yes/No); Arabic RTL for RPT-09, 10, 12, 13, 14, 15, 16: both sheets `rightToLeft`, Arabic sheet names and headers, Arabic state/status/boolean labels, null age blank, summary in Arabic |
| Formula safety | note text `=HYPERLINK(...)` and customer `+SUM(A1)`: CSV prefixed with `'`, XLSX a typed string, no `<f>` |
| Worker | after a reassignment between request and generation: RPT-10 keeps the recorder's row, RPT-11 follows the new owner, RPT-16 drops it for the old owner |

## 7. Timings on `cawp-db` (474k cases, 11.8k promises, 31.4k payments, 1.27M notes; read-only; median of 5)

| Preview | Rows in result | Median | Min–max |
|---|---:|---:|---:|
| RPT-09 aidi.dev | 11,839 | **272 ms** | 260–604 |
| RPT-09 aidi.dev {"due_date_from": "2025-09-01", "due_date_to": "2026-08-31"} | 9,339 | **218 ms** | 218–233 |
| RPT-09 bader.b | 572 | **74 ms** | 69–74 |
| RPT-09 aidi.dev {"sort": "due_date", "page": "100"} | 11,839 | **266 ms** | 254–279 |
| RPT-09 aidi.dev {"state": "overdue", "q": "محمد"} | 479 | **144 ms** | 140–160 |
| RPT-10 rasha.s | 1,144 | **35 ms** | 34–39 |
| RPT-11 rasha.s | 76 | **11 ms** | 11–12 |
| RPT-11 marwan.a | 0 | **9 ms** | 8–11 |
| RPT-12 aidi.dev | 173 | **530 ms** | 526–749 |
| RPT-12 aidi.dev {"case_state": "active"} | 173 | **430 ms** | 418–786 |
| RPT-12 bader.b | 71 | **316 ms** | 313–320 |
| RPT-12 rasha.s | 74 | **508 ms** | 500–545 |
| RPT-13 aidi.dev | 6,968 | **325 ms** | 312–380 |
| RPT-13 rasha.s | 504 | **270 ms** | 265–282 |
| RPT-13 aidi.dev {"sort": "-approved_amount"} | 6,968 | **310 ms** | 304–315 |
| RPT-14 aidi.dev | 164,488 | **301 ms** | 293–308 |
| RPT-14 rasha.s | 67,370 | **222 ms** | 217–244 |
| RPT-14 aidi.dev {"sort": "customer_name", "page": "500"} | 164,488 | **749 ms** | 642–784 |
| RPT-15 aidi.dev | 171,456 | **115 ms** | 113–168 |
| RPT-15 rasha.s | 67,874 | **94 ms** | 91–97 |
| RPT-15 aidi.dev {"age": "181_plus"} | 480 | **6 ms** | 6–7 |
| RPT-15 aidi.dev {"sort": "-age_days"} | 171,456 | **260 ms** | 246–269 |
| RPT-16 rasha.s {"callback_from": "2026-09-01", "callback_to": "2026-09-30"} | 128 | **17 ms** | 15–89 |
| RPT-16 rasha.s | 128 | **16 ms** | 15–26 |
| RPT-16 marwan.a | 0 | **14 ms** | 13–17 |

| Selector (aidi.dev; RPT-16 rasha.s) | Candidates | Median | Min–max |
|---|---:|---:|---:|
| RPT-09 recorder | 97 | 36 ms | 31–84 |
| RPT-09 portfolio | 109 | 34 ms | 30–34 |
| RPT-13 owner | 73 | 150 ms | 105–229 |
| RPT-13 note_status | 106 | 79 ms | 77–120 |
| RPT-15 owner | 95 | 102 ms | 89–121 |
| RPT-12 portfolio | 173 | 2 ms | 1–2 |
| RPT-16 recorder | 1 | 7 ms | 6–8 |

| Export (full, fresh snapshot, batches of 500) | Rows | CSV | XLSX | Arabic XLSX | CSV size |
|---|---:|---:|---:|---:|---:|
| RPT-09 aidi.dev | 11,839 | 0.69 s | 0.95 s | 0.98 s | 5.1 MB |
| RPT-12 aidi.dev | 173 | 0.93 s | 0.93 s | 0.91 s | 0.0 MB |
| RPT-13 aidi.dev | 6,968 | 0.92 s | 0.97 s | 0.81 s | 2.5 MB |
| RPT-14 aidi.dev | 164,488 | 3.42 s | 5.78 s | 5.72 s | 43.6 MB |
| RPT-15 aidi.dev | 171,456 | 3.95 s | 6.86 s | 7.01 s | 47.9 MB |
| RPT-16 rasha.s | 128 | 0.53 s | 0.04 s | 0.04 s | 0.1 MB |
| RPT-10 rasha.s | 1,144 | 0.11 s | 0.09 s | 0.09 s | 0.5 MB |
| RPT-11 rasha.s | 76 | 0.02 s | 0.02 s | – | 0.0 MB |

- **RPT-12** costs two parallel seq scans of `clints` (the case and payment aggregates) for the totals and again for the page.
  - The first version's visibility `EXISTS` probed every case of each unsupervised portfolio: bader.b took 980 ms.
  - The semi-join from the scope users' cases brought it to 316 ms, with the same portfolio set for all six actors.
- **RPT-14 deep pages** sorted by name take ~0.75 s: a top-N over 164k rows.
- **Largest exports:** RPT-14/15 (164k / 171k rows) take ≤ 4 s in CSV and ≤ 7 s in XLSX, at about 25–48k rows/s.

## 8. Migrations

**None.** 0044 and 0045 were not needed:
- every preview is under 0.8 s on the full data;
- the promise, callback and neglect paths use existing indexes (`ix_paymentdates_payment_date`, `ix_comments_clint_id`, `ix_clints_last_comment_live_asc`).

One option was considered and not taken: an index on `"paymentDates"(create_at)` would turn RPT-09's default `-created_at` page into an index walk (~270 ms → a few ms). The totals would still scan the whole scoped set, so it was not worth a migration. Both numbers can be released to other streams.

## 9. Open items

1. **RPT-11 key (decision).** No legacy screen calls `/payment_date/user`, so the screen-key rule gives `reports_performance`. On `cawp-db` only ADMIN holds it, so the collectors this report is meant for cannot open it. Options:
   - grant `reports_performance` to collectors;
   - or accept `report_collectors_currentUser_paymentDates` as an any-of key, since RPT-11 is the "my cases" variant of the collectors' promise screen. That would be a one-line change in `reports_v2_b.py`.
2. **RPT-16 default window.** The framework's `DateRange` has no default, so v2 lists all callbacks when no window is given. The legacy screen always sends the current month, so the frontend should default to the first and last day of the current Riyadh month.
3. **Imported promises:** 11,824 of the 11,839 live promises are the imported baseline, with fulfilment unknown. RPT-09/10/11 show them as open or overdue with `imported_baseline = true`, and count them in `imported_baseline_count`. They do not count as fulfilled.
4. **Latest note status.** The promise and case reports show the status of the latest effective note, like legacy. A newer plain note's status replaces an older classified one's, because every legacy note carries a status. A "latest *classified* note" field was not added.
5. **Age semantics.** `age_days` is complete 24 h periods, as in legacy. It is not a Riyadh calendar-day difference, and an export can cross a 24 h boundary relative to an earlier preview.
6. **Legacy findings, carried forward and not fixed** (legacy endpoints stay):
   - `/payment_date`, `/payment_date/export` and `/portfolios` are unscoped;
   - `/payment_date/currentUser` `totalAmount` sums debts;
   - `/payment_date/user` and next-calls do not filter deleted rows;
   - next-calls compares UTC days, and its export uses the latest comment only;
   - the `/portfolios` "remaining" subtracts unapproved payments;
   - legacy totals are floats.
7. **Integration (lead):** nothing to apply; the module is discovered on first use. Add RPT-09…16 to the frontend hub after the backend.
