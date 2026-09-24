# Phase 5 — frontend (calling screens)

Date: 2026-09-24. Branch `phase-5-calling`. Nothing is committed. Scope: `CAWP-frontend/**` (plus this report). No change to `CAWP-API/**`, `db/**` or `docs/`, and `cawp-api` / `cawp-worker` were not restarted.

The contract comes from `review/phase5-{foundation,click-to-call,campaigns,evidence,softphone}.md`, checked against `CAWP-API/api/calling*.py` and `services/calling/*`. The softphone built in P3 (`src/services/softphone/`, `src/components/softphone/`) is integrated, not rebuilt: the calling workspace reads its status, and the new live-call dock sits beside its panel.

The real Issabel PBX was **never contacted**. Every live call went through the local test PBX (`pbx-test`, pjsua auto-answer agent **7101**, loopback "customers") and the `calling` service, which logged `PBX pbx-test:5039`. Only ZZ-TEST loopback numbers created for this run (`9665000099xx`) were dialled. Both containers are stopped.

**Blocker found (API, §6.1):** the running `cawp-api` process was started at 07:53 UTC, before `api.calling_campaigns` and `api.calling_sip` were added to `api/__init__.py` (08:57 UTC). On :9797 `GET /v2/campaigns` and `GET /v2/me/sip-credentials` answer 404. The campaign screens were therefore verified live against the **softphone e2e API** (the same code, all blueprints registered, on its throwaway test database) with the same test PBX and calling service (§4.2). They will work on :9797 after a restart of `cawp-api`.

## 1. Screens, routes and keys

| Screen | Route | Guard key | Nav | Main file |
|---|---|---|---|---|
| Calls (my calls / all calls in my scope, detail panel) | `/calls` | `calls` | Daily operations | `views/phase5/calls/index.tsx`, `CallDetail.tsx` |
| Calling workspace | `/calling` | `calling_any` (derived: `calls_make` \| `campaigns`) | Daily operations | `views/phase5/calling/index.tsx`, `CampaignParticipation.tsx` |
| Campaigns (list + create) | `/campaigns` | `campaigns_any` (derived: `campaigns` \| `campaigns_manage`) | Daily operations | `views/phase5/campaigns/index.tsx`, `CampaignForm.tsx` |
| Campaign (live view, pool, roster, settings, lifecycle) | `/campaigns/:id` | `campaigns_any`; the page needs `campaigns_manage` (a member is sent to the workspace) | — | `views/phase5/campaigns/CampaignDetail.tsx` |
| Call review (uncertain calls, review flags) | `/call-review` | `call_review` (derived = `calls_manage`) | Intelligence & control | `views/phase5/review/index.tsx` |
| Calling admin (status, extensions, legacy drift, calling hours, do-not-call) | `/calling-admin` | `calling_admin` (derived = `calls_manage`) | Intelligence & control | `views/phase5/admin/index.tsx` |
| Case → Calls tab + "Log a call" | `/clients/details/calls?id=` | `calls` \| `calls_make` (log: `calls`) | — | `views/phase5/case/CaseCallsPanel.tsx`, `wrapup/LogCallDialog.tsx` |
| Case → contacts: "Call" per phone contact | case Details tab | `calls_make` | — | `views/phase5/case/CallContactAction.tsx`, `callability.ts` |
| Docked live-call panel + wrap-up (every page) | — | `calls` \| `calls_make` | — | `views/phase5/live/*`, `wrapup/WrapupDialog.tsx` |
| PBX CDR import (bulk kind `pbx_cdr_import`) | `/jobs/new/pbx_cdr_import` | the kind's `allowed` (`calls_manage`) | entry on `/calls` | `views/phase2/bulk/params.tsx`, `paramsLogic.ts`, `RowDetails.tsx` |

- Two nav items cannot share one key (React keys and the active-route highlight), so `/call-review` and `/calling-admin` use the derived keys `call_review` and `calling_admin`, both true exactly when `calls_manage` is. The derived keys live in `utils/v2/derivedPermissions.ts` like the earlier phases' ones; real keys are never changed.
- `BULK_JOB_KEYS` gains `calls_manage` (the `pbx_cdr_import` kind key), so a calls manager sees `/jobs`.
- Icons (Lucide): `Phone`, `Headset`, `Megaphone`, `ShieldAlert`, `SlidersHorizontal`.

### 1.1 Click-to-call on the case page
- Each phone contact of the Phase 1 contacts panel gets a "Call" icon (`calls_make`). E-mails get none.
- **Why a contact cannot be called** is shown in the API's own rule codes (`services/calling/rules.py`), mapped to bilingual sentences (`p5.refusal.<code>`):
  - known before any click, from `GET /v2/me/calling` and `GET /v2/cases/<id>/calling-blocks`: `no_extension`, `case_archived`, `case_locked`, `do_not_call`, `contact_blocked`, `destination_invalid`, the calling window (`calling_windows_missing` / `calling_window_closed` / `calling_holiday`), `calling_reserved` (my own call is still open) and `transport_not_connected`. Contact-level reasons show under the row; case- and user-level ones show once above the list. The button is then disabled with the reason as its tooltip.
  - everything else is the API's answer to `POST /v2/calls` (closed/paid, a later callback with its Riyadh times, a pending supervisor review, a reservation race...), shown under the row in words.
- The browser sends only `{case_id, contact_id}`, never a number. A placed call opens the docked panel with the POST answer as its first view.

### 1.2 The docked live-call panel (`live/LiveCallDock.tsx`)
- Mounted once in `components/layouts/Layouts.tsx` (lazy); it renders nothing without `calls` / `calls_make`.
- It shows the call just placed, a campaign call handed to me, or any attempt that still holds my reservation (`GET /v2/me/calls/active`, every 15 s while the tab is visible and on focus), so a reload or another tab finds the call again.
- It polls `GET /v2/calls/<id>/live` at the API's `poll_after_ms` (1 s live, 5 s uncertain, stop when final) with **`If-None-Match`**; a 304 keeps the view. The API does not expose the `ETag` header cross-origin (§6.2), so the body's own `etag` is used.
- Content (`LiveCallPanel.tsx`):
  - an honest status line per state: "Authorized and queued. The calling service has not sent it to the PBX yet", "Submitted: the PBX accepted the request. This is not a connection yet", "You answered. Only your leg is up", "The customer's line answered. It is not a conversation until both legs are bridged", "Connected: the PBX shows you and the customer in one bridge", ended with or without a conversation, failed, cancelled;
  - the step timeline from the attempt's PBX timestamps (authorized → submitted → agent ringing → agent answered → customer dialled → customer answered → **bridged** → ended), with "not seen" for skipped steps;
  - **uncertain** explained: the reason, "nothing is assumed", the reservation stays and the wrap-up waits for reconciliation or a supervisor;
  - the reason and the talk time when final (an unknown duration is "Unknown", never 0), open review flags, the PBX events (collapsible), and the evidence rule;
  - actions only as the API allows: **Cancel** while `actions.can_cancel` (still queued), **Wrap up** when `actions.can_wrapup`. While a call is live it says that the system cannot hang up; hang up on the phone.
- It sits at the bottom inline-end corner, moves beside the softphone panel when that is open (both stay usable), and stays under modal dialogs. Closing a live or unwrapped call only minimizes it.

### 1.3 Wrap-up (`wrapup/WrapupDialog.tsx`)
- Opens by itself once when the call ends (the dock), or from the dock, "My calls", the case Calls tab or the workspace. It reloads the live view first (fresh revision and `actions.wrapup_outcomes`).
- **Outcome**: the seven outcomes; those not in `wrapup_outcomes` are disabled with "Needs PBX bridge evidence; this call shows none". The API enforces the rule (409 `conversation_not_evidenced`) and its refusal is shown in words with the draft kept.
- **Note** (required: text, reachability, status filtered by the case's contract and reachability like the legacy note form, contact method defaulting to mobile), optional **callback** (Riyadh date and time; a date alone means 09:00).
- **Promise**: only with `connected`, only after ticking "The debtor explicitly agreed to pay", and only with `promises` / `clints_PayemntDates`; amount + due date go through the Phase 1 promise rules. "Promise discussed" shows that it never creates a promise and offers no promise fields.
- Wrong number: optional reason; when the number is the default contact the API names the replacement candidates and the dialog asks for the new default. Do-not-call: optional reason and what it blocks.
- **Locked / archived case**: the dialog says before saving that the wrap-up is recorded (outcome and note text) and the reservation released, but the note, callback, promise and blocks wait for a supervisor's review; after saving, a warning toast repeats it, and the call detail shows "Recorded; case changes await review" with the deferred items.

### 1.4 Manual call log (`wrapup/LogCallDialog.tsx`)
"Log a call" in the case header and on the case Calls tab (`calls`): Riyadh date and start time (up to 30 days back), direction, the number called (optional; needed for a wrong number), duration in minutes and seconds (blank = saved as **unknown**, stated in the form), outcome, note, callback, the agreed promise (same rules), optional transcript text. A locked or archived case refuses a manual log (the API's 409) and the dialog says so upfront.

### 1.5 Calls screen (`/calls`)
- Tabs "My calls" (`GET /v2/calls/mine`) and "All calls in my scope" (`GET /v2/calls`, so a supervisor sees the team through the case scope).
- State tabs All / Live / Ended / Failed / Uncertain / Cancelled with exact counts (one extra `page_size=1` read without the state filter), filters type, outcome, wrap-up pending/done, collector (team scope), case chip, Riyadh from/to, paging, the selected call: all in the URL hash.
- Detail panel (`CallDetail.tsx`): facts (number only with `clints_details`), the timeline, the wrap-up (outcome, note, callback, promise link, blocks, review state) or "Wrap up" when it is pending and mine / I hold `calls_manage`, the transcript, the evidence (legs answered/bridged/cause, AGI decisions, event count), and **PBX evidence** (`GET /v2/calls/<id>/pbx-evidence`): the CDR status (`available`, `unavailable` = unknown, never "no records", `disabled`), CDR rows with dispositions and known/unknown durations and how they were linked, imported records with their job and row, and recordings with download/play controls only under the recording keys (the unchanged legacy `/cdr/recording` route).
- Entry points: "Import PBX call records" (the `pbx_cdr_import` wizard, hidden unless `allowed`) and "Call review" (`calls_manage`).

### 1.6 Calling workspace (`/calling`)
- **My calling**: my extension, the Riyadh calling window, the softphone's registration state (read from the P3 engine; the header chip owns it), auto-answer preference, and whether click-to-call is possible now or the first reason why not.
- **My active call** (`GET /v2/me/calls/active`, every 3 s): state, case, "Show the live panel", "Wrap up".
- **My campaigns** (`GET /v2/campaigns?mine=1`); one campaign opens directly. The participation panel:
  - Ready / Paused / Offline set my declared state (`POST …/heartbeat {state}`); while the page is open and I am not offline the lease is renewed every `heartbeat_after_ms` **without an Idempotency-Key** (the route makes it optional; a key would store a response per beat);
  - `GET …/me` is polled at `poll_after_ms`: my effective state and why, the lease (live until / lapsed at), the heartbeat period, the extension;
  - manual mode: **Next call** (`POST …/next`) places at most one call or says why not (`no_eligible_case`, the window...); auto mode: the **wrap-up delay countdown** and "Next call now";
  - the current campaign call is handed to the dock (and its wrap-up opens there);
  - today's counts (calls, conversations = bridge evidence, customer answered, wrapped up, failed, outcomes);
  - the rule in words: leaving the page stops the heartbeat, the lease lapses and no new call is placed; **a call in progress is never cut**.

### 1.7 Campaigns for supervisors (`/campaigns`, `/campaigns/:id`)
- List by status (active / draft / running / paused / draining / stopped / all in the hash), "New campaign" (`campaigns_manage`); the clients list also gets **"New campaign from this filter"**, which hands the list's current query (the same parameters the list sends, paging and the list's always-present defaults removed) to the form.
- Create / edit (`POST`, `PATCH` with `expected_revision`): name; pool = contracts and/or portfolios and/or the clients-list filter (chips, removable) plus the list's column filter (account number, identity, name, portfolio, contract, amount with the list's operators); settings next call auto/manual, wrap-up delay 0–600 s, max calls per case per day 1–50, cooldown 0–10080 min, lease 10–300 s; members on create. The form states the scope rule and "progressive only".
- Lifecycle buttons start/resume, pause, drain, stop, each behind a confirmation that says what happens (queued calls cancelled, a call already sent always continues and still needs its wrap-up; a stopped campaign cannot restart); the toast reports cancelled queued calls and an immediate drain.
- **Live view** (`GET …/live` at `poll_after_ms`): the calling service's PBX connection and epoch, KPIs (calls, conversations, customer answered, wrapped up, live, outstanding), outcomes, members (state and why, declared state and lease, current call with case, today's counts), the pool summary (size, callable now, remaining today, **excluded by reason**, evaluated at / cached), and all-time uncertain / awaiting wrap-up.
- Pool tab (`GET …/pool`, reason filter, dispatch order), Roster tab (add members, remove with a reason; a live call continues), Settings tab (settings, pool definition with names, history and stop reason).

### 1.8 Supervisor review (`/call-review`)
- **Uncertain calls** (`GET /v2/calling/uncertain`): why (reason in words), whether bridge evidence exists, the service's last check, whether the reservation is held. **Resolve**: ended or failed, a required reason, "release the reservation" (default on, explained). Without bridge evidence the dialog says the conversation outcomes stay unavailable.
- **Review flags** (`GET /v2/calling/review`): open / resolved / all, kind tabs with exact counts, each flag with the outcome and deferred items of a locked-case wrap-up, the note text, whether it blocks calling; **Resolve** with a required reason and the kind's own hint (apply the deferred change through the normal screens first).

### 1.9 Calling admin (`/calling-admin`)
- **Service status** (`GET /v2/calling/status`): connected (with the note that it is the AMI session at its last report, not a call guarantee), host, epoch and its source, connected at, restart flag, disconnect code, active reservations, AGI allow/deny for 24 h, in-flight calls, the calling window.
- **Extensions**: active / released / all with search; assign (extension, user picker `GET /v2/calling/users`, reason, `expected_owner_id` read from the extension's history, optional replace), reassign, release (reason), history (who/when/why, legacy holders). Legacy conflicts after an assignment are reported.
- **Legacy extensions**: the drift list classified (`adoptable`, `legacy_invalid`, `legacy_owned_by_other`, `differs_from_owned`, `mirror_missing`, `user_inactive`), "Adopt" only on adoptable rows (reason).
- **Calling hours**: the window editor (days, start inclusive, end exclusive, `24:00`, overnight), holidays, "Every day, all day", save with a required reason and the loaded revision, the history, the live open/closed state in Riyadh time.
- **Do-not-call**: the cases in my scope with a do-not-call wrap-up, each case's block (active / lifted, reason, since) and **Lift** with a reason. The API has no list of all blocks (§6.4); the case Calls tab also shows and lifts the case's block.

### 1.10 PBX CDR import
The Phase 2 wizard is driven by `GET /v2/bulk/kinds`, which lists `pbx_cdr_import`, so the kind, its sources and its twelve mapping fields appear without code. Added: the params form (required reason, source name, PBX time zone, match window 0–3600 s) with its validity and body, the row details in the preview/results (attribution in words — CRM tag, number and time, number —, call time, disposition, known/unknown duration and billed time, a link to the call, conflict fields, first row of a duplicate), bilingual labels for the kind's row codes (`unmatched`, `ambiguous`, `duplicate_in_file`, `already_imported`, `source_conflict`, `invalid_calldate`, `invalid_duration`, `invalid_billsec`, `billsec_exceeds_duration`, `attribution_changed`) and param names. Entry point: "Import PBX call records" on `/calls`.

### 1.11 Permissions screen
`constants/permissions.phase1.ts` now mirrors `core/permissions.py` `NEW_PERMISSIONS` exactly (30 keys, same order): the Phase 4 `reports_performance`, `reports_calls` (they were missing) and the Phase 5 `calls`, `calls_make`, `calls_manage`, `campaigns`, `campaigns_manage`. The section is titled "Workflow permissions (Phases 1–5)". The verify script compares the two lists.

## 2. Files

**New:**
- `src/services/v2/p5Types.ts`, `p5Api.ts` (`callsApi`, `fetchLiveCall` with ETag, `callReviewApi`, `callingAdminApi`, `campaignsApi`), `p5Errors.ts` (`describeP5Error`, `refusalText`, `describeP5Fields`, `DISPATCH_RULES`)
- `src/views/phase5/labels.ts`, `parts.tsx`
- `src/views/phase5/live/{callDock.ts, useLiveCall.ts, LiveCallPanel.tsx, LiveCallDock.tsx}`
- `src/views/phase5/wrapup/{draft.ts, caseFacts.ts, fields.tsx, WrapupDialog.tsx, LogCallDialog.tsx}`
- `src/views/phase5/case/{callability.ts, CallContactAction.tsx, CaseCallsPanel.tsx}`
- `src/views/phase5/calls/{index.tsx, CallDetail.tsx}`
- `src/views/phase5/calling/{index.tsx, CampaignParticipation.tsx}`
- `src/views/phase5/campaigns/{index.tsx, CampaignForm.tsx, CampaignDetail.tsx, poolFilter.ts}`
- `src/views/phase5/review/index.tsx`, `src/views/phase5/admin/index.tsx`
- `scripts/verify-phase5.cjs`

**Changed (additive):**
- `services/v2/http.ts` (`v2Patch`, `patch` method, Phase 5 conflict codes), `services/v2/index.ts` (exports)
- `utils/v2/derivedPermissions.ts` (`calling_any`, `campaigns_any`, `call_review`, `calling_admin`; `calls_manage` in `BULK_JOB_KEYS`), `utils/v2/links.ts` (`calls` tab)
- `configs/navigation.config/index.ts` (5 items), `configs/routes.config/routes.config.ts` (6 routes), `configs/navigation-icon.config.tsx` (5 icons)
- `components/layouts/Layouts.tsx` (the dock), `views/clients/detail/ClientDetail.tsx` (Calls tab, "Log a call", reload after a wrap-up on this case), `views/phase1/history/ContactsPanel.tsx` (Call + refusals), `views/clients/list/ClientsTable.tsx` ("New campaign from this filter")
- `views/phase2/bulk/{params.tsx, paramsLogic.ts, RowDetails.tsx}` (`pbx_cdr_import`)
- `constants/permissions.phase1.ts`, `views/permissions/form/Fields.tsx` (section title)
- `locales/lang/{ar,en}.json`: **905 keys under `p5.*`, 5 under `nav.p5.*`**, and 14 `p2.*` labels for the new kind; identical key sets; insertions only (a JSON round trip reproduces the files).
- `scripts/verify-phase0.cjs`, `verify-phase3-flows.cjs`, `verify-phase4.cjs`: the sidebar count expectation (§4.3).

Not touched: `src/services/softphone/**`, `src/components/softphone/**` (read only), other screens.

## 3. Build, type check, lint

| Check | Before | After |
|---|---|---|
| `npm run build` | passes | **passes** (only the usual chunk-size warning) |
| `tsc --noEmit` | 4654 | **4654**, and the per-file counts are identical to HEAD (436 files); every new file has 0 |
| `eslint . --ext .ts,.tsx` | 823 errors / 2487 warnings | **823 / 2487**; every new and changed file has the same count as before, the new files 0 / 0 |
| Linux case-sensitive build | — | sources `rsync`ed onto a `Case-sensitive APFS` disk image, `vite build`: **passes**; the negative control (`./LiveCallPanel` imported as `./Livecallpanel`) fails with `Could not resolve "./Livecallpanel"` |

## 4. Verification

| Script | Result |
|---|---|
| **`verify-phase5.cjs`** (new), live API :9797 + cawp-db | **128/129**. The one failure is the campaign section: `GET /v2/campaigns` is 404 on the running API (§6.1). Log `logs/screens/phase5-ui/verify.log`, `verify.json`, 61 screenshots (ar and en) |
| **`verify-phase5.cjs` `ONLY=campaign,auto`** against the softphone e2e API (same code, throwaway `cawp_test_call3_e2e`) | **23/23**. Log and 10 screenshots in `logs/screens/phase5-ui/campaign-e2e-db/` |
| `verify-phase5-softphone.cjs` | **78/78** (203 s) |
| `verify-phase0.cjs` | **47/47**, after 1 expectation change (§4.3) |
| `verify-phase1.cjs` | **58/58** |
| `verify-phase1-reviewer.cjs` | **9/9** |
| `verify-phase2.cjs` | **119/119** |
| `verify-phase3.cjs` | **124/124** (expectation change §4.3). One earlier run had 3 failures, all English labels read while the page was still Arabic (the language-toggle flake Phase 4 already described); `ONLY=attention` passed 4/4 and the next full run 124/124. An earlier run also failed while I was editing sources (Vite hot reload reset the page). |
| `verify-phase4.cjs` | **186/186** (expectation change §4.3) |

### 4.1 What `verify-phase5.cjs` covers (sections runnable with `ONLY=`)

The live sections start the test PBX and the `calling` service when they are not running (`docker compose --profile pbx-test up -d pbx-test`, then `calling` with `CALLING_ENABLED=1` on the app database) and stop them at the end of a full run (`KEEP=1` keeps them). Setup through the API: ZZ-TEST loopback contacts on ZZTEST-P1-0003 (`…9916` rings 3 s then answers for 20 s, `…9912` busy, `…9914` answers and hangs up after 3 s, `…9911` answers with echo, `…9918` blocked for the refusal check) and on ZZTEST-P1-0004 (`…9924`, made the default so a campaign can only dial it). Nothing else is ever dialled.

- **screens:** the six screens plus the review and admin tabs and the case Calls/contacts, in ar (RTL) and en (LTR), no error state, no page error; the five nav items; aidi.dev sidebar `[9,13,11]`.
- **gating:** aidi.collector (no calling keys) sees none of the five items, every URL (also `/campaigns/<id>`) lands on access-denied, no dock, and the API agrees (403). A stubbed collector-like role with `calls`, `calls_make`, `campaigns` sees Calls, Calling workspace and Campaigns but not Call review / Calling admin (`/calling-admin` → access-denied).
- **perms:** the mirror equals `core/permissions.py` (30 keys, same order); the permissions screen shows the five calling keys in Arabic and the "Phases 1–5" title.
- **admin:** calling hours saved through the UI (save disabled until a reason; revision +1; open, 24/7); **extension 7101 assigned to aidi.dev through the UI** (owner shown, `GET /v2/me/calling` → 7101, `can_call` true); the history with the reason; service status connected with the epoch (ar and en); the legacy drift list equals the API page, adopt only on adoptable rows.
- **refusal:** the blocked contact shows "Cannot call now: the contact is blocked (wrong number)" (`contact_blocked`) with Call disabled, in ar and en; callable test contacts are enabled; the API refuses the blocked contact with 409 `contact_blocked`; an API-only refusal (stubbed `callback_scheduled`) is shown under the row with the Riyadh times.
- **c2c (live, pjsua agent 7101):** "Call" → the dock opens with "Authorized and queued… not sent yet" (never "connected"); the dock went `authorized > customer_dialing > bridged > ended`, the bridged step done; **ETag polling**: `If-None-Match` ×25, 200 ×4, 304 ×22; ended by evidence (`customer_hung_up`); the wrap-up opened by itself with all seven outcomes (bridge evidence); "promise discussed" shows it never creates a promise; "connected" offers the agreed promise; saved connected with a note → the API has the wrap-up, the case note id and the reservation released (`wrapup`); the dock shows the wrapped call; **the note is on the case timeline** (notes tab); the case Calls tab lists the call with its detail.
- **busy:** `customer_busy`, no bridge; only no answer / busy / do not call offered, "connected" disabled with the reason and the bridge rule; with the live view stubbed to offer "connected", the API refuses it (**409 `conversation_not_evidenced`**) and the dialog shows "The PBX shows no bridged conversation for this call…", keeping the draft; then **busy** saved.
- **locked:** a short call ends; the case is locked (legacy route); the wrap-up says the wrap-up is recorded and the case changes wait for a supervisor; saved connected → the API has `needs_review`, `case_locked`, no case note, reservation released; the **review flag** is listed and **resolved** with a reason in `/call-review`; the case is unlocked.
- **manual:** "Log a call": 2 min 5 s, the number, connected with an **explicitly agreed promise** (10.00, tomorrow), note and transcript → a manual call (125 s `reported`, transcript), the promise created through the promise rules (then cancelled again by the script); a second log with do-not-call and a blank duration (announced as unknown; saved `null`) → the case block; the contacts panel says the case cannot be called (`do_not_call`), the case Calls tab shows the block; **calling admin → Do-not-call lists it and lifts it** with a reason (history kept).
- **calls:** "My calls" and the All tab equal the API; state + outcome filters in the hash equal the API and survive a reload; the detail panel shows the timeline, the wrap-up, the leg evidence and the PBX records with their CDR status; "All calls in my scope" equals `GET /v2/calls`.
- **cdr:** "Import PBX call records" opens the `pbx_cdr_import` wizard (from `GET /v2/bulk/kinds`); a one-row CSV tagged `cawp:<attempt>` is uploaded, the eight columns are mapped from the headers, the params step shows reason/source/time zone/window (validate disabled until the reason), the job runs to the preview, which attributes the row to the call by its **CRM tag** (`attempt_tag`, eligible) with a link to the call; the job is then cancelled (nothing imported).
- **uncertain:** an echo call is bridged; the script stops the `calling` service → the dock shows **uncertain** (`calling_service_stopped`) with the explanation; `/call-review` lists it ("Bridge evidence exists", reservation held); resolve needs a reason; resolved ended with the reservation released (`reconciled`); then wrapped up (connected) from the Calls screen; the service is started again.
- **release:** **extension 7101 released through the UI** (reason required); aidi.dev has no extension; the history keeps both rows.

### 4.2 The campaign sections (on the e2e API)

Because `/v2/campaigns` is missing on :9797, `ONLY=campaign,auto` ran against the softphone e2e stack: `cawp-softphone-e2e-api` (the real `app.py` with every blueprint, on the throwaway `cawp_test_call3_e2e` database rebuilt by `setup_db.py`), a second Vite server on 127.0.0.1:5179 with `VITE_API_URL=http://localhost:9796`, the same `pbx-test`, and the `calling` service started with `CALLING_DATABASE_URL=…/cawp_test_call3_e2e`. The e2e role got `campaigns`, `campaigns_manage`, `calls_manage`, `clints_contacts_edit` for the run (test database only; the next softphone run rebuilt the database), and the user `e2e.agent` got 7101.
- **campaign:** created through the UI form (pool = portfolio + the account-number column filter, manual mode, delay 0, max 50/day, cooldown 0, the signed-in user as the only member) → draft with that pool; **started** through its confirmation; in the workspace **Ready** → "waiting for next"; **Next** placed exactly one campaign call on the pool case to the test number; bridged → ended; wrap-up connected; the **live view** shows 1 call, 1 conversation, outcome connected, the member and the pool summary (size 1); the pool and roster tabs; **Drain** with nothing outstanding → stopped / drained.
- **auto:** an automatic campaign (delay 45 s) started; Ready → the calling service placed the call by itself; after the wrap-up the **countdown** ran (45 → 42 s, state wrap-up); the **heartbeat** renewed the lease while the page was open (a keyless `{}` renewal); Offline ended the lease; the campaign stopped.

### 4.3 Expectation changes in earlier scripts
The aidi.dev sidebar count per group goes from `[6,13,9]` to **`[9,13,11]`** (Daily operations + Calls, Calling workspace, Campaigns; Intelligence & control + Call review, Calling admin) in `verify-phase0.cjs`, `verify-phase3-flows.cjs` and `verify-phase4.cjs`. The Phase 0 stubbed roles are unaffected (none holds a calling key).

### 4.4 Test data written (all ZZ-TEST)
- ZZTEST-P1-0003: contacts `9665000099{11,12,14,16,18}` (`…18` blocked with a reason), about 27 test calls by aidi.dev with wrap-up notes (connected, busy, a locked-case wrap-up recorded for review, the supervisor-resolved uncertain call), manual logs (one with a promise the script cancelled, one do-not-call whose block it lifted), the lock/unlock history of the locked-case check.
- ZZTEST-P1-0004: contact `966500009924`, now its **default** contact (the case had no default before), so a campaign on it can only dial the test number.
- Review flags: the locked-case flags and three `late_evidence` flags (the echo call ended after its wrap-up) are all resolved with a reason.
- Calling hours: the policy is now **24/7** ("ZZ-TEST phase 5 frontend verification…", several revisions; the policy has no delete). Before this run no policy existed (calling closed).
- Extension 7101: assigned to aidi.dev and released, several times (history kept); `users.extension` of aidi.dev is empty again.
- `pbx_cdr_import` jobs: one per run, cancelled after the preview (nothing imported).
- The e2e campaign runs wrote only to `cawp_test_call3_e2e`, which the softphone script rebuilt afterwards.


## 5. Decisions

- **One global live-call dock** instead of a per-page panel: a call follows the collector across pages, reloads and tabs (it is re-found through `GET /v2/me/calls/active`), and there is exactly one place that opens the wrap-up automatically (once per attempt). Its z-order keeps it under modal dialogs; it moves beside the softphone panel.
- **Known refusals are shown before the click** in the API's own codes, but only where the screen already has the facts; every call is still decided by `POST /v2/calls`, whose refusal is shown the same way.
- **Outcomes not in `actions.wrapup_outcomes` are disabled** with the reason, rather than offered and refused; the API's refusal path is still handled and shown (verified with a stubbed live view).
- **The heartbeat is the one v2 POST sent without an Idempotency-Key** (the route makes it optional; a key would store a response every 10 s).
- **Campaign detail is for managers**; a member (`campaigns` only) opening `/campaigns/:id` is sent to the calling workspace.
- **Do-not-call admin list** is built from the scoped do-not-call wrap-ups (the only way a block is created) because the API has no block list.
- **ETag from the body** when the header is not exposed.

## 6. API issues (exact requests)

1. **The running API has not registered the campaign and SIP blueprints.** `cawp-api` started at 2026-09-24T07:53:03Z; `api/__init__.py` (with `api.calling_campaigns`, `api.calling_sip`) was changed at 08:57Z.
   - `GET http://localhost:9797/v2/campaigns` (aidi.dev token) → `404 {"status":404,"message":"Not Found: http://localhost:9797/v2/campaigns"}`; the browser's CORS preflight (`OPTIONS …/v2/campaigns/<id>`) is also 404, so the page sees a network error.
   - `GET /v2/me/sip-credentials` → `404 {"status":404,"message":"Not Found: …"}`; the softphone chip stays "Starting" on :5177.
   - Fix: restart `cawp-api` (not done, as instructed). Then `ONLY=campaign,auto node CAWP-frontend/scripts/verify-phase5.cjs` runs the campaign checks on the real database.
2. **`ETag` is not exposed cross-origin** (`GET /v2/calls/<id>/live` has no `Access-Control-Expose-Headers`). The new screens use the body's `etag` (same value) and get 304s. The P3 softphone's `fetchLiveAttempt` sets `data.etag = headers.etag || undefined`, which drops the body's tag, so the softphone's per-line live panel never sends `If-None-Match` (a full 200 each poll). One-line fix in `services/softphone/api.ts` (P3's file, not changed here) or expose the header.
3. **The campaign pool refuses some parameters the clients list sends**: `validate_pool` only knows the keys in `campaign_pool.py`; the list also sends `customeFilterArchivingStatusesValue` (default `0`) and `customeFilterBranchesValue` without `[]` when empty. "New campaign from this filter" drops empty values and the defaults; a non-default archive-state filter will be refused with `422 {"pool.filter.customeFilterArchivingStatusesValue": "unknown"}`.
4. **No list of calling blocks**: only `GET /v2/cases/<id>/calling-blocks`. The admin tab derives the cases from `GET /v2/calls?outcome=do_not_call` (scoped).
5. **The user picker `GET /v2/calling/users` needs `calls_manage`**; the campaign member picker therefore uses the legacy `/uesrs` lookup (which still embeds role permission JSON, Phase 3 open item) and the API validates members (`422 member_not_allowed`).
6. A failed wrap-up (409) and the corrected retry with the same Idempotency-Key and a different body are accepted (the store keeps only successes). This is what the UI relies on (one key per dialog opening), noted for the record.

## 7. Open items

- **Restart `cawp-api`** so `api.calling_campaigns` and `api.calling_sip` are served on :9797; then rerun `ONLY=campaign,auto node CAWP-frontend/scripts/verify-phase5.cjs` (it needs the test PBX and the calling service, which it starts) to verify the campaign screens on the real database. Until then the campaign screens and the workspace's campaign card show a network error on :5177, and the softphone chip stays "Starting".
- **Softphone ↔ dock:** the softphone's per-line live panel keeps its own minimal view (P3); the dock is the full one. The softphone's missing `If-None-Match` (§6.2) is P3's one-line fix.
- **Softphone presence → readiness** (P3 open item 6) is not wired: readiness is set on the workspace, not from the softphone's registration state.
- **Supervisor hang-up** does not exist (the AMI account has no `call` write); the UI says so on live calls.
- **Calling hours / holidays** are enforced by the dialplan for hours and by the app only for holidays (P0 open item 9); the editor says so.
- **Pool filter from the clients list** drops the list's default archive-state value; a non-default archive-state filter is refused by the API (§6.3).
- **The dock overlaps the page's inline-end column** (e.g. the contacts' action icons) while expanded; it can be minimized. A resizable context panel (design system) would be the next step.
- **Dev server:** still running on :5177 (not touched). The test PBX, the calling service and the e2e containers are stopped.

