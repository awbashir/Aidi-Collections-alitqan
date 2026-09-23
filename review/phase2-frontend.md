# Phase 2 — frontend (reviewed bulk jobs)

Date: 2026-09-23. Branch `phase-2-bulk-jobs`. Nothing is committed. Scope: `CAWP-frontend/**` only. No changes to the API, DB, Docker or docs, and no container restarts.

The contract comes from `review/phase2-{framework,assign,archive,notes,inquiry}.md`. I checked it against the live API on :9797 (worker running) and against `CAWP-API/api/bulk.py`, `core/bulk/service.py` and `services/bulk_*.py`. Where the reports and the live API differ, I followed the live API (see §6).

## 1. Screens, routes and keys

| Screen | Route | Guard key | Main file |
|---|---|---|---|
| Bulk jobs (list + detail panel) | `/jobs` | `bulk_jobs` (derived) | `src/views/phase2/jobs/index.tsx`, `JobDetailPanel.tsx` |
| Wizard, steps 1–4 (source, upload, mapping, params) | `/jobs/new/:kind` | `bulk_jobs` + the kind's `allowed` | `src/views/phase2/bulk/NewJobPage.tsx` |
| Wizard, steps 5–9 (validating, preview, confirm, running, results) | `/jobs/run/:id` | `bulk_jobs` (the API decides visibility) | `src/views/phase2/bulk/RunJobPage.tsx` |

- **The `bulk_jobs` key is derived.** It is true when the role holds any bulk kind key or `bulk_jobs_all`, which mirrors `GET /v2/jobs` (`service.any_bulk_permission`). It lives in `src/utils/v2/derivedPermissions.ts`, and only three places use it: `AuthorityGuard`, `VerticalMenuContent` and `usePermissions`. Real keys are never changed or removed.
  - Kind keys mirrored: `clints_bulk_lock`, `clints_lock`, `clints_sepcifyUser`, `clints_move_archive`, `clints_archive_move`, `clints_archive`, `clints_UpdateByExcel`, `case_corrections_request`, `follow_ups_manage` and `client_query`.
- **Nav.** "Bulk jobs" (Lucide `ListChecks` icon) sits in Intelligence & control, after Case corrections.
- **Wizard permission.** On a kind the user may not use, the wizard shows the 403 state with `required_any`, taken from `GET /v2/bulk/kinds` (`allowed` and `permissions`).

## 2. Entry points

Every button is hidden unless `GET /v2/bulk/kinds` returns `allowed` for that kind (cached per signed-in user). The legacy dialogs stay available. Where a reviewed flow exists, the new flow is the default entry and the legacy one is labelled "classic".

| Screen | Entry | Kinds / sources | Legacy |
|---|---|---|---|
| Clients list (`views/clients/list/ClientsTable.tsx`) | "Reviewed bulk actions" menu | **Selected (N) or the current filter:** Assign, Round-robin, Archive, Lock, Unlock, Add note.<br>**From a spreadsheet:** Assign, Archive, Lock, Unlock, Add note | The selected-row buttons are grouped under a "classic" caption. The Excel-menu items for assign, archive and notes get a "(classic)" suffix |
| Archive screen (`views/archive/list/PortfoliosTable.tsx`) | "Reviewed restore" menu: selected rows, the current archive filter (`list: archive`), or a spreadsheet | `restore` | "Restore by Excel" gets "(classic)". The table gained row checkboxes; before this it had no selection |
| Portfolios (row actions) | "Reviewed" menu: Archive the portfolio…, Round-robin its unassigned cases… | `archive` (portfolio source), `round_robin` (portfolio prefilled) | The legacy archive icon's tooltip gets "(classic)" |
| Follow-ups | "Close out late imported follow-ups". The current owner, contract and portfolio filters are prefilled | `followup_closeout` | — |
| Inquiry (`/client_query`) | "Inquiry from a spreadsheet" | `inquiry` | — |
| Corrections | "Request corrections from a spreadsheet" | `attribute_corrections` | — |

**Current filter.** The clients and archive lists pass their filter exactly as they send it. The query string is built by the same axios instance with the same params object (`listQueryString` → `BaseService.getUri`), and the API drops paging. The list's count travels with it. The preview compares the two, and the check shows 4 = 4 on the ZZ-TEST filter.

## 3. Wizard architecture

**Services** (`src/services/v2/`):
- `bulkTypes.ts`: wire types.
- `bulk.ts`: `bulkApi`, which covers kinds, collectors, upload (multipart), create, job, preview, rows, confirm, cancel, retry, reopen and jobs. Also `downloadArtifact`, a blob download with the bearer token; 403/409/410 come back as `V2Error`. And `listQueryString`.
- `bulkErrors.ts`: `describeBulkError`, bilingual `p2.err.*`, nested field problems for `params` and `mapping_invalid` in `p2.field.*` / `p2.problem.*`, and `bulkCodeLabel` (`p2.code.*`, with the server message as fallback).

**Additive changes to shared Phase 1 code:**
- `InlineError`, `ErrorState` and `ActionDialog` take an optional `describe` / `describeError`.
- `V2Error.isConflict` also treats `job_not_ready` as a conflict.
- `lookups.archiveReasons`.

**Views** (`src/views/phase2/bulk/`):
- `NewJobPage` handles steps 1–4. The launch context comes from the calling screen in router state (`BulkLaunch`): case ids, the filter with its count, the portfolio, a preferred source, and prefilled params.
  - Step 1 lists every source the kind accepts. A source the context can't supply is disabled, with the reason.
  - Step 2 (`UploadStep`): drag-and-drop, then `POST /v2/uploads` with purpose = the kind, one Idempotency-Key per chosen file. It shows the inspection: type/encoding/delimiter/size, sha256, rows, formula cells, the sheets table (rows, columns, header row, formula cells, incomplete, unnamed columns), and the expiry. The **"Not scanned"** warning always shows. Then pick the sheet.
  - Step 3 (`MappingStep` + `mapping.ts`): one select per `mapping_fields` entry, with required/match markers and guesses from the headers, plus the sample table with the mapped columns highlighted and formulas flagged. For `attribute_corrections` there is also a key → column editor (`params.attributes`) and the `blank_cells` choice.
  - Step 4 (`params.tsx` + `paramsLogic.ts`), one form per kind:
    - **assign:** collector picker from `/v2/bulk/assign/collectors` plus an optional reason. From a spreadsheet the login comes from the file.
    - **round-robin:** portfolio or contract, and an ordered collector list with up/down/remove, plus an optional limit and reason.
    - **archive:** reason select and a note.
    - **restore / lock / unlock:** a required reason.
    - **notes:** a contract select that filters statuses (like the legacy dialogs), reachability, status, contact method, text, a Riyadh callback date and time sent as `YYYY-MM-DDTHH:mm:00+03:00`, and a contact number.
    - **inquiry:** `match_mode`.
    - **close-out:** an optional reason (the server default otherwise) and owner/contract/portfolio.
    - **attribute_corrections:** a required reason.
  - "Validate and preview" creates the job, then does a `replace` navigation to `/jobs/run/:id`.
- `RunJobPage` handles steps 5–9. The job is polled every 1.5 s while it is active, and the step follows the status.
  - The stepper always shows 9 steps. Steps a job doesn't need are marked "not needed" (upload and mapping without a spreadsheet; preview, confirm and running for the read-only inquiry).
  - **Preview:**
    - exact counts (total, eligible, excluded, error);
    - `by_code` with bilingual labels (click one to filter);
    - `kind_summary.by_target` per collector;
    - the filter-count comparison;
    - preview and errors CSVs;
    - "valid until" (Riyadh time);
    - paged rows with eligibility tabs, a code filter and a row/value search, all in the hash (`view`, `rows`, `code`, `q`, `page`).
  - **Confirm:**
    - summary, and the `expected_revision` shown;
    - an "accept partial" checkbox that is required (confirm stays `aria-disabled`) when `needs_accept_partial`;
    - 409 `job_not_ready` and `revision_conflict` show the "Changed by someone else" box with "Reload the latest version";
    - `preview_expired` offers a reload, which lands on the expired stage with Reopen.
  - **Running:** progress and cancel (202 "cancel requested" note).
  - **Results:**
    - succeeded / skipped / failed / not-run counts;
    - who confirmed, and whether partial was accepted;
    - the per-collector outcome table;
    - results, errors and preview CSVs;
    - retry and reopen when `actions` allows;
    - outcome tabs with exact counts (`page_size=1` per outcome);
    - per-kind row details: owner → target and position; archive reason / archived at; lock before → after; note status and callback with a link to the case follow-ups; days late and the outcome note for close-out; attribute before → after with a link to the correction.
  - **Inquiry** shows a matches table: sheet row, match k of n, matched by, the case link, owner, portfolio/contract, flags, exact outstanding balance, last note and follow-up. Plus the results CSV.
- `JobActions`: cancel (optional reason), retry (`expected_revision`) and reopen. Each dialog opening is one Idempotency-Key.
- `parts.tsx`: the stepper, status/eligibility/outcome badges (glyph + colour), count cards (one lead card with the orange rail), progress bar and artifact buttons.

**Jobs screen:**
- Kind, creator ("Me" or a user) and Riyadh date filters.
- `counts_by_status` chips that add up to "All".
- Page, filters and the selected job are kept in the hash.
- The detail panel shows the facts, row counts, attempts (phase, worker, start/end, outcome, rows), params, artifacts, and cancel / retry / reopen.
- A resume button opens the wizard at the right step: "Review and confirm" for a ready job, "Follow progress", or "Open results".

**Translations.** 521 keys under `p2.*`, plus `nav.p2.jobs`, in ar and en. Every row and error code the kinds emit has a bilingual label. The change is additive and the JSON formatting is kept.

## 4. Verification

| Check | Result |
|---|---|
| `npm run build` | Passes (only the usual chunk-size warning) |
| `tsc --noEmit` | 4656 → **4655** errors. No file gained an error, and the new files have 0 (`AuthorityGuard` went from 2 to 1) |
| `eslint . --ext .ts,.tsx` | Errors **824 → 824**. Warnings 2487 → **2486**. New Phase 2 files: 0 errors, 0 warnings |
| Linux case sensitivity | Sources copied with `tar` into `/work` in a throwaway `node:22-alpine`, then `npm ci && npm run build`: **exit 0** |
| `verify-phase0.cjs` | **47/47**, after 2 expectation changes (below) |
| `verify-phase1.cjs` | **58/58** |
| `verify-phase1-reviewer.cjs` | **9/9** |
| **`verify-phase2.cjs`** (new) | **119/119**. Log in `logs/screens/phase2/verify.log`, results in `verify.json`, 49 screenshots |

**Expectation changes in `verify-phase0.cjs`**, both caused by the new nav item:
- The aidi.dev sidebar item count changes from `[6,9,8]` to `[6,9,9]`.
- The "collector-like role" fixture holds `client_query`, the inquiry kind's key. It now also sees the Intelligence & control group, which contains only Bulk jobs.

**What `scripts/verify-phase2.cjs` (+ `verify-phase2-flows.cjs`) covers.** It reuses the phase 1 helpers and runs only on ZZTEST-P1-0001…0004 and files it generates. Sections can be run on their own with `ONLY=`.
- **Screens:**
  - `/jobs` in ar (RTL) and en (LTR);
  - the nav item in the third group;
  - all 9 wizard kinds render with 9 steps;
  - no page errors.
- **Lock (ar) → unlock (en)**, by selection from the clients list:
  - the wizard starts on "2 selected";
  - "validate" stays disabled until the reason is filled;
  - preview: 2 eligible → confirm → results: 2 succeeded;
  - the cases are verified locked, then unlocked.
- **Current filter:**
  - with no selection, the filter source is offered;
  - preview rows = the list count = 4;
  - the stored query is the list's own (portfolio filter, no paging);
  - the job is cancelled.
- **Spreadsheet assign (account → login):**
  - a generated CSV with 1 ZZ-TEST row and 1 unknown account;
  - "not scanned" is shown and there are 2 rows; both columns are matched from the headers;
  - preview: 1 eligible, 1 `not_found_or_not_permitted`, the target login on the row, the per-collector summary;
  - **accept partial:** confirm stays disabled until the box is ticked, and the job records `accept_partial: true`;
  - case 3 goes to `rasha.s`, then is assigned back to aidi.dev through the collector picker.
- **Archive → restore of ZZTEST-P1-0004:**
  - archived from the clients list (reason select);
  - restored on the archive screen, by selection (the case was on the first page);
  - both states are verified.
- **Notes batch with a callback:**
  - the contract → status filter;
  - the callback shown and sent as `2026-09-25T10:30:00+03:00` (the request body is captured);
  - after confirmation the case has an open `note` follow-up due at `07:30Z`;
  - the results link opens the case's follow-ups.
- **Spreadsheet inquiry:**
  - from `/client_query`, straight to results: 1 match and 1 `not_found_or_not_permitted`;
  - the case link;
  - the results CSV downloads, with `source:` columns.
- **Attribute corrections:**
  - from `/corrections`, with the key → column editor;
  - the preview shows before → after;
  - after confirmation: 1 **pending** `attribute` correction, and the case attribute is unchanged;
  - cleanup: **rejected by aidi.reviewer in the Phase 1 UI**.
- **Close-out:**
  - from `/follow-ups`, preview only: 1,971 rows, 1,950 eligible, 21 `case_locked`;
  - then cancelled; not confirmed and 0 processed.
  - A network guard aborts, and fails the run on, any confirm request for a `followup_closeout` job. It never fired.
- **Round-robin and portfolio archive,** from the portfolios row menu:
  - collector order and reordering;
  - preview plan per collector in the given order; cancelled;
  - "archive portfolio" starts on the portfolio source, previews 4 cases, and is cancelled.
- **Jobs screen:**
  - the `#kind=lock` hash filter and the chip sums;
  - the selected job kept in the hash across a reload;
  - attempts (validation + execution) and 8 row counts;
  - the **results CSV downloads**;
  - "Open results" resumes at step 9, and a ready job resumes at the preview with "Continue to confirm".
- **409s:**
  - two tabs: tab B cancels while tab A is on confirm, then A's confirm gets **HTTP 409** `job_not_ready`. The UI shows "Changed by someone else…", and reload shows the cancelled job;
  - a stale version (the confirm request re-sent with `expected_revision - 1`) gets **409 `revision_conflict`**. The UI shows "your version 5, now 6. Nothing was run", and the job stays ready;
  - via the API: confirming without `accept_partial` gets 422 `accept_partial_required`.
- **aidi.collector:**
  - no bulk menu on the clients list;
  - `/archive`, `/portfolios`, `/follow-ups` and `/corrections` go to access-denied;
  - `/jobs/new/{lock,assign,archive,notes,followup_closeout,attribute_corrections}` show the 403 state;
  - a direct `POST /v2/bulk/lock` gets 403.
  - It *does* see the inquiry button and `/jobs` (own jobs only, 0 rows); see §6.1.

**Final test-data state:**
- Cases 1–4 are unlocked and not archived; 1–3 are owned by aidi.dev and 4 is unassigned.
- There are 3 `followup_closeout` jobs, all `cancelled` and never confirmed: 1 by the lead and 2 by the script runs.
- The only open job is the lead's earlier `attribute_corrections` "live test" (ready).
- The script writes notes, follow-ups (case 1), lock/assign/archive history rows and rejected corrections on the ZZ-TEST cases, and the corresponding jobs appear in `/jobs`.

## 5. Decisions

- **The wizard is a page, not a dialog.** After creation everything is keyed by the job id (`/jobs/run/:id`), so a reload, a shared link or the jobs screen resumes at the right step. Steps 1–4 are local state; a reload there starts again.
- **The route/nav key for `/jobs` is derived** (§1). No new server key was needed.
- **Buttons are hidden unless `allowed`.** This uses the server's `allowed` rather than a frontend key list, because the any-of lists already differ from the reports (§6.2).
- **Assign test collector.** `aidi.collector` is not an eligible target (it is not in `/v2/bulk/assign/collectors` for aidi.dev). The spreadsheet assign therefore uses `rasha.s`, the in-scope collector the lead's own live tests used, and assigns the case straight back.

## 6. API findings (exact requests)

1. **aidi.collector holds `client_query`, which is a bulk kind key.** So it gets the spreadsheet inquiry and the jobs screen, and the API agrees:
   - `GET /v2/bulk/kinds` as aidi.collector returns `inquiry: allowed true` and every other kind `false`;
   - `GET /v2/jobs` as aidi.collector returns **200** `{"items":[],"total":0,…}`.

   The brief expected the collector to see *no* bulk entry points. The UI mirrors the API as instructed. If collectors should not get spreadsheet inquiry, remove `client_query` from `inquiry.permissions` (API), and from `BULK_JOB_KEYS` for the nav. **Lead decision.**
2. **The reports have drifted from the live API.** The frontend follows the live API.
   - `GET /v2/bulk/kinds` shows `attribute_corrections.permissions = ["case_corrections_request","clints_UpdateByExcel"]`; `review/phase2-inquiry.md` says `case_corrections_request` only.
   - `GET /v2/bulk/assign/collectors` exists, returning `{"items":[{id,user_name,name}],"total":139}`, and `preview.kind_summary.by_target` is returned. `review/phase2-assign.md` §8 says neither exists yet.
3. **`revision_conflict` cannot happen naturally on a ready job.** The job revision does not move while it is `ready`, and cancel/confirm by another tab change the status first. So the realistic conflict is 409 `job_not_ready`, for example:
   - request: `POST /v2/bulk/jobs/<id>/confirm {"expected_revision":6,"accept_partial":true}` after another tab cancelled the job;
   - response: `409 {"code":"job_not_ready","error":{"status":"cancelled"}}`.

   The UI treats both as conflicts. The `revision_conflict` path was exercised by re-sending the request with an older revision.
4. **Inquiry row order.** Extra matches of a row are numbered `1e9 + n×1000 + k`, after all sheet rows (stream D open item 2). The table shows a "sheet row / match k of n" column so they can still be read.
5. **The download file name comes from the artifact list, not from `Content-Disposition`.** The API is cross-origin and does not expose that header. The result is the same name either way.
6. **The legacy archive list had no row selection.** I added selection (checkboxes) to the archive table for reviewed restore. Nothing else about the legacy list changed.

## 7. Open items

- **Collector access to inquiry and jobs** (§6.1): lead decision.
- **Close-out** stays preview-only until the user decides. The script can never confirm it.
- **Selection across pages.** The legacy clients-list selection resets when the page changes, so "selected cases across searches and pages" is limited to one page. Use the filter source for larger sets.
- **The filter source is evaluated when the worker validates**, not at the moment of the click (framework item 6). The preview says when the list count and the preview row count differ.
- **The wizard's steps 1–4 don't survive a reload** (an upload stays valid for 7 days, but you pick the file again).
- **Stream D's framework items** (inquiry duplicates keeping result columns, and row ordering) would improve the inquiry table if they are done.
- **Dev server:** still running on :5177 (PID 55329).
