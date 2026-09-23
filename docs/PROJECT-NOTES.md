# Aidi Collections (CAWP) — important project notes

Read this before changing anything. It is the target product contract and the rules for getting there. Last updated 2026-09-23.

## 0. Non-negotiable rules

1. **Extend, don't replace.** The current Flask API (`CAWP-API/`) and React app (`CAWP-frontend/`) are production code with real data. Add new blueprints, services, tables, columns and screens next to the existing ones. Do not rewrite, remove or re-shape working logic unless it is broken and the fix is proven equivalent. Keep API wire contracts byte-compatible (envelopes, legacy names like `clints`/`uesrs`, status codes). Prove parity with `tools/api_snapshot.py --compare logs/golden_before.json` and the `CAWP-API/tests/*_parity.py` harnesses before swapping any implementation.
2. **Additive database changes only.** Use new tables/columns, triggers or views with scripts under `db/` (see `db/README.md`). Never drop or rename a column the current code reads. Anything that touches money must reconcile exactly.
3. **Infrastructure runs in Docker.** New services (e.g. TURN/STUN, workers) go in `docker-compose.yml`.
4. **PBX = Issabel** (Asterisk underneath). The existing CDR/recording path (`CAWP-API/issable/db_connection.py`, routes `/cdr`, `/cdr_by_date`, `/cdr/recording` in `app.py`) stays. New calling features are built on top of it.
5. **Browser SIP softphone**: build our own inside `CAWP-frontend`, with similar UI and features to a standalone WebRTC softphone (feature list in §3.2). **Do not use, copy or vendor [CONNXTA/cloudsip.app](https://github.com/CONNXTA/cloudsip.app) code**. Write it from scratch in our React/TypeScript stack and design system. Take calling logic and design evidence from [awbashir/Aidi-Collections](https://github.com/awbashir/Aidi-Collections), a private Node/TypeScript rebuild. Port its *concepts* into this Flask/React stack. Do not swap runtimes (see §3.3).
6. **Design system**: the Aidi AI Enterprise system (`design-reference/`, `CAWP-frontend/src/assets/aidi/`), with Arabic RTL and English.
7. **Never fabricate state.** Unknown stays unknown: CDR duration, call outcome, PBX connection, scan status, AI output when unconfigured.
8. **Asia/Riyadh** civil dates decide overdue status and day bounds.

## 1. Application shell and navigation

- Sidebar groups:
  - **Daily operations**: dashboard, attention queue, cases, follow-ups, calls, calling workspace, campaigns, plus bulk/calling screens that depend on permissions.
  - **Recovery & team**: plans, promises, reports, team, requests.
  - **Intelligence & control**: AI, quality, settings, plus administration/imports/jobs/PBX screens that depend on permissions.
- Header: global case search, refresh, language toggle, notification/attention shortcut, profile, mobile navigation.
- Most detailed list states live in the URL hash (filters, page, selected case), so reload, Back and shared bookmarks keep them.
- Consequential bulk changes always show a confirmation preview first.

## 2. Functional feature catalogue

### 2.1 Dashboard and daily operations

- Dashboard: outstanding debt, approved recovered money, active cases, recovery rate, approved-payment trend, case-status distribution, priority cases, team recovery, links into work. Uses scoped PostgreSQL aggregates and a recorded payment-date series. Charts may abbreviate axis labels, but exact values must stay available.
- Daily attention queue groups actionable facts: late follow-ups, missed promises, unassigned cases, unscheduled active cases, pending payment approvals. One case can have several reasons. Queue counts and list links respect actor scope. A do-not-call case may still appear for internal review, but **being in the queue never authorizes a call**.

### 2.2 Case list, search, inquiry and profile

- **List/search**: bounded server-side pages, stable sorting, full-result count, literal English/Arabic search. Filters: case/account/identity, permitted additional fields, status/priority/archive/lock/assignment/organization/date/amount. Selected criteria combine with explicit AND. State is restored from the URL. Shows the exact outstanding balance, derived from approved payments and debt corrections.
- **Case creation**: pick an authorized active portfolio, debtor/account with required identity details, starting debt and case metadata. Never silently merge ambiguous debtor identities or reuse source keys.
- **Case detail**: read-only overview plus sections for account/debtor details, debt and delinquency, contacts, notes, follow-ups, payments, promises/plans, documents, activity, and assignment/state histories. Refresh after a mutation. Keep drafts when a save fails. Discard stale responses after a case or scope change.
- **Inquiry**: exact account/identity lookup, or authorized literal contact/attribute lookup, including archived results where permitted. Spreadsheet inquiry uses a private upload, column mapping, row-level matching, source preview and results. Knowing an identifier never grants broad case access.
- **Corrections**: reviewed updates with a reason to identity/profile/additional fields, original-debt impact and delinquency. Keep before/after values and actors. Financial corrections reconcile exactly. These are separate from ordinary status/priority edits.
- Still open: the case acceptance matrix gaps (source fields, operators, child screens), plus full legacy list and mobile parity.

### 2.3 Assignment, archive, locks and batch changes

- Assign one case to an eligible collector. Record the current owner and an immutable assignment history. Revisions prevent stale overwrites.
- **Bulk assignment**: selected cases across searches and pages, spreadsheet account/login mapping, and deterministic round-robin of unassigned eligible cases. Flow: private review of eligible/excluded rows → explicit confirmation (partial acceptance allowed) → per-case outcomes and saved run history.
- **Archive/restore**: a reversible case state with a required reason and history. Independent of closed and paid status. Variants: single case, selected cases, whole portfolio, spreadsheet, all through reviewed jobs.
- **Lock/unlock**: a separate control with reason and history. A lock protects affected mutations. It is not a do-not-call flag.
- Bulk variants recheck current case revision, authorization, reason and call reservations at execution time.
- Active, uncertain or awaiting-wrap-up calls can block ownership or state changes. Previews may show known exclusions, but execution rechecks them. **Never clear reservations to force a bulk change.**

### 2.4 Contacts, notes, follow-ups and documents

- **Contacts**: several per debtor, with type, number and active/default/block flags, and permissioned read/edit. Exactly one valid default. Revision checks. Safe behavior when a debtor has several cases.
- **Notes**: chronological, plain or classified. Configurable comment status, reachability, contact method, an optional contact snapshot and callback time. Recording a note can atomically update the current follow-up. Keep the original text and a correction/retirement history with reasons. Keep the real author and time.
- **Follow-ups**: schedule, complete or reschedule, with an explicit next action and history. Show late and upcoming work separately. A missed callback stays a recorded fact even if the case can't be called.
- **Note batches** (selected cases or spreadsheet): shared note, classification and callback; source-row errors; explicit partial confirmation; per-case results.
- **Documents**: private text and binary files (supported PDF, PNG, JPEG and text paths). Upload inspection; scoped list/read/download/preview; display name; retirement with a reason; purge of eligible payloads. A database row or a filename is not proof of malware scanning or valid rendering.

### 2.5 Payments, promises and repayment plans

- **Payment lifecycle**: a collector submits amount, type/method and actual payment date. A permitted reviewer approves or rejects it with attributable evidence. Pending and rejected amounts never count as recovered. A reversal is a separate compensating event, never the deletion of an approved receipt. Approval checks the exact remaining balance under concurrent writes. The approval desk and case history show status, reviewer/time, source and correction history.
- **Promise lifecycle**: records an explicitly agreed amount and due date. States: open, overdue, fulfilled, cancelled. "Promise discussed" as a call outcome does **not** create a promise. Fulfillment needs allocated approved payment evidence from the same case. Promise evidence has its own capacity ledger and never creates a payment.
- **Repayment plans**: weekly/monthly installments with exact splitting and approved-payment allocation. Plans don't change the debt and don't count as recovered money.

### 2.6 Internal requests and team administration

- **Requests**: collectors raise case-linked requests routed to legal, finance or operations. Authorized staff review, resolve or reopen them with reasons and an immutable event trail. Lists need scoped case selection, filters, paging, a saved location, and an "imported baseline" label where source history is incomplete.
- **Administration**: branches, contracts, portfolios, users, roles/grants, supervisor-to-user and supervisor-to-contract relationships, targets (fixed amount or percentage), and lookup categories (payment/contact/comment/archive/delinquency values).
  - Keep inactive parents that are still referenced, for history. Block new invalid links.
  - Disabling a user or changing grants revokes effective access in existing sessions.
  - Forms use optimistic revisions, idempotency, bilingual validation and protected employee fields.
  - Native login history has its own permission, separate from identity management.
  - Still open in the administration matrix: exact legacy table fields, exports and secondary screens.

### 2.7 Data imports, exports and background jobs

Portfolio and operation-specific CSV/XLSX sources follow one pattern:

1. Upload to private storage for a named purpose. Inspect type, size, formula risk and scan status.
2. Map source columns explicitly. Keep raw values, row numbers and source identity.
3. Validate in a durable worker. Show source-row and case-level errors. Save a reviewable preview without changing business records.
4. Require confirmation, with an explicit choice for partial application. Run in bounded idempotent batches, rechecking permission, source expiry, version and current case state.
5. Publish scoped reconciliation, error files and provenance. Support safe retry, cancel and reopen. A dedicated retention worker purges expired files; audit and provenance stay.

- Variants: portfolio import, inquiry, assignment, notes, archive/restore, additional-field corrections, PBX CDR review.
- Jobs: queued/running/retrying/succeeded/failed/cancelled states, attempt history, current selected detail, permissioned artifact download. (The current `core/jobs.py` + `/jobs/<id>` async path is the base to extend.)
- Report exports use a fresh private database snapshot, so a preview and a later export can differ if data changed in between. CSV is formula-safe. The collections report also has an Arabic XLSX variant.
- Real antivirus and external storage integration are separate commissioning items.

### 2.8 Reports and analytics

Reports need:

- permissioned filters, stable paging and exact total/count definitions;
- source-aware columns, and exports that match the full authorized result;
- saved filter/page state.

Definitions:

- Always distinguish the current case owner, the note/payment recorder, the historical owner and the approval actor.
- Approved recovery excludes pending, rejected and reversed money.
- Date-only payment/promise filters are inclusive civil dates. Timestamp note/CDR filters use Asia/Riyadh day bounds.
- An unknown CDR duration is unknown, not zero.

| ID | Report / core definition |
|---|---|
| RPT-01 | Scoped user roster, role/status, assignment totals and protected personal fields/targets. |
| RPT-02 | Unarchived cases with organization, assignment, account, debt and last-note fields. |
| RPT-03 | Archived cases with archive actor, reason, date and exact case totals. |
| RPT-04 | Team/global classified note activity; latest matching note selected across the full filtered set. |
| RPT-05 | Notes recorded by the current user, regardless of current case ownership. |
| RPT-06 | All scoped collections, with payment and approval metadata. |
| RPT-07 | Collections recorded by the current collector. |
| RPT-08 | Approved-only collections, not the pending approval queue. |
| RPT-09 | Team/global promises, amounts, due dates and states. |
| RPT-10 | Current recorder's promises. |
| RPT-11 | Promises on currently owned, unarchived cases, regardless of recorder. |
| RPT-12 | Portfolio case counts, ages and exact debt/recorded/approved totals. |
| RPT-13 | Unarchived cases with any payment record, including unapproved evidence. |
| RPT-14 | Unarchived cases with no payment records, regardless of balance. |
| RPT-15 | Age since latest effective note; no note gives unknown/null age. |
| RPT-16 | Recorded callbacks on currently owned, unarchived cases. |
| RPT-17 | My all-time collection and activity summary. |
| RPT-18 | Dated collector dashboard: notes, approved receipts, current assignments and classifications. |
| RPT-19 | Collector performance against configured contract targets, with rankings. |
| RPT-20 | Daily approved recovery by current owner and contract. |
| RPT-21 | Daily promised amounts by current owner and contract, split by resolution. |
| RPT-22 | Collector comparison with exact target and case/contract provenance. |
| RPT-23 | Hourly note and PBX activity modes, with reviewed CDR attribution. |
| RPT-24 | Aggregate dashboard with scoped summary, trends, contracts and collectors. |
| RPT-25 | Call detail records with metadata/date filters; recording access is a separate permission. |

### 2.9 Human calling, campaigns and PBX evidence

- **Manual logging**: record a human call's start, outcome and duration, with an optional transcript. Outcomes: connected, no answer, busy, promise discussed, dispute, wrong number, do-not-call. Wrap-up saves the note, callback and (where offered) an explicitly agreed promise in one reviewed transaction. Wrong number and opt-out block later calls.
- **Calling settings/admin**: scoped user extension choices, preferences, server calling windows and extension ownership.
- **Click-to-call is extension-first**: ring the authorized agent extension, then route the permitted customer destination through a restricted dialplan. An AMI "accepted" response means *submitted*, not connected or completed.
- **Call lifecycle**: durable preparation, dispatch authorization, attempt/reservation, PBX agent/customer channel evidence, bridge membership, wrap-up and uncertainty holds.
  - Never infer a successful conversation from two answered legs alone.
  - A lost PBX connection or unclear topology stays *unknown* until reconciled.
  - A restart does not auto-release an active reservation.
- **Campaigns**: team pool, collector roster, concurrency, readiness heartbeat, one reservation per collector/case, automatic or manual next call, wrap-up delay, pause and drain/stop. At dispatch, recheck do-not-call, future callbacks, closure/payment, scope and reservation. Simulated answer/busy/end controls are clearly labeled and never contact a PBX.
- **Browser SIP**: a real human softphone (WSS/DTLS/ICE, device controls, two-way audio, multi-tab ownership). This is a *target*, not yet an accepted feature. See §3. The OpenAI Realtime browser voice lab is separate training, not a human debt call.
- **CDR/recordings**: private, scoped metadata and recording access; reviewed PBX spreadsheet import; source/case/leg attribution; duration may stay unknown. Real Issabel configuration, routing, live media and end-to-end recording evidence need external commissioning.

### 2.10 AI assistance and quality review

- Configured OpenAI assistance drafts call scripts, case guidance, narratives, call summaries, coaching and follow-up text.
- Transcript-based quality review uses configurable English/Arabic templates with cited evidence, a private WAV reference library, human approval history and downloadable review reports. The voice lab is fictional training.
- If the API isn't configured, show a clear "unconfigured" state. Never fabricate output.
- **Generated text is a draft.** A human reviews and edits it before explicitly saving it as a case note. Generating text alone never changes a case, books a payment, creates a promise, authorizes a call or sends a message.
- Transcript content and any supplied audio need an explicit privacy/retention policy.

## 3. Browser SIP softphone plan (Issabel, built in-app)

### 3.1 What we already have (keep)

| Piece | Where | Behavior |
|---|---|---|
| CDR list | `GET /cdr`, `GET /cdr_by_date` → `Issable_db` | MySQL `asteriskcdrdb.cdr` and `call_center.calls`, parameterised, JWT-protected |
| Recording download | `GET /cdr/recording?recordingPath=` | fetched from the PBX via paramiko/SSH, path-validated |
| Case call history UI | `CAWP-frontend/src/views/clients/detail/components/CallsRecordClientTable.tsx` | per-client CDR table |
| Config | `CAWP-API/core/config.py` `CDR_*` | env-driven. **Remove the hard-coded default `CDR_PASSWORD`; env/secret only.** |

### 3.2 Our own softphone (built in `CAWP-frontend`)

**Do not use cloudsip.app.** No code, files, assets or CDN links from it; it is GPL-3.0. Build the softphone ourselves in React/TypeScript and the Aidi Enterprise design system (Arabic RTL and English). It should offer similar UI and features to a standalone WebRTC softphone:

| Feature | Our version |
|---|---|
| SIP over secure WebSocket (WSS), register/unregister, connection status | Status chip in the header (registered / connecting / offline / failed) with a diagnostics panel: WebSocket state, registration, ICE, microphone. |
| Dialpad and DTMF | Docked panel. DTMF during a call. Outbound dialing only to an authorized case contact through the server (see §3.4), never free text to an arbitrary number. |
| Multi-line calls, hold/resume, mute, switch line | Line list with auto-hold when switching lines. Each line is linked to its case and call attempt. |
| Transfer | Only to internal extensions/supervisors. No blind transfer to external numbers. |
| Incoming/answer/reject | Auto-answer only for calls tagged with our attempt ID (dispatcher/campaign). Other calls ring normally. |
| Audio devices, input level meter, ringtone/sounds | Microphone/speaker/ringer selection, stored per user in the API. Test tone and mic meter. |
| Browser checks and WebRTC diagnostics | HTTPS, microphone permission, WebRTC support, codec/ICE checks before registering. |
| Presence/availability | Ties into campaign readiness heartbeat (ready / paused / wrap-up / offline). |
| Call log and call threads | Built from the existing `/cdr*` data plus our call attempts, scoped by permission. No browser-local call log. |
| Contacts | The case's debtor contacts (§2.4) with active/default/block flags, not a separate phone book. |
| Recording | Recorded on Issabel (MixMonitor) and played through `/cdr/recording` under its separate permission. No browser-side recording or IndexedDB storage. |
| Keyboard shortcuts, dark mode | Answer/hang up/mute/hold shortcuts; follows the app theme. |

Other rules for the softphone:
- **No SIP password in the browser's storage.** The API issues per-user, per-extension credentials after JWT auth, and only to the extension's owner (extension ownership, §2.9).
- SIP library: JsSIP (MIT) or SIP.js (MIT) from a pinned npm package, wrapped in our own service module (e.g. `src/services/softphone/`). No CDN script tags.
- Multi-tab ownership: only one tab holds the registered SIP session (Web Locks / BroadcastChannel). Other tabs show status only.
- Wrap-up (§2.9) opens automatically when a call ends, linked to the same case and attempt.

### 3.3 Source: awbashir/Aidi-Collections (logic and evidence)

The rebuild is Node 24/TypeScript with its own PostgreSQL schema. **Do not import its runtime.** Port its designs into Flask services and new tables:

- `docs/human-calling.md`: persistent authenticated AMI session; one-shot dispatch authorization; extension-first restricted origination; durable attempt/leg evidence; PBX process epoch; bridge membership; reconciliation inventory; uncertainty holds; wrap-up.
- `docs/issabel-dialer.md`: dedicated AMI account (TLS, originate + call/user/system read, no command/config), custom contexts in `extensions_custom.conf`, `UserEvent(CollectionsCall, AttemptId…)` correlation, progressive dialer (one reservation per collector, no speculative calls).
- `docs/voice-roadmap.md`: the deployment validation checklist. `examples/human-dialplan-policy.json`: dialplan policy shape.
- `docs/LEGACY_PARITY.md`, `docs/discovery/*-acceptance.md`, `docs/report-definitions.md`: acceptance matrices for §2.

### 3.4 How the softphone fits the call lifecycle

- The softphone is **the agent's extension endpoint**, nothing more. Click-to-call and campaigns stay extension-first:
  1. The API authorizes the attempt (scope, do-not-call, calling window, reservation).
  2. AMI originates to the agent's WebRTC extension.
  3. The softphone answers (auto-answer only for calls tagged with our attempt ID).
  4. The restricted dialplan dials the customer through the trunk and emits `UserEvent`s.
- A browser "confirmed" session event is agent-leg evidence only. Customer answer comes from PBX events.
- Issabel side (commissioning, not code): enable HTTPS/WSS in `http.conf` (TLS 8089) with a trusted certificate; WebRTC extension (PJSIP `webrtc=yes`, or chan_sip `transport=wss, avpf=yes, encryption=yes, icesupport=yes`, DTLS cert); opus/ulaw codecs; firewall for WSS and RTP. Add STUN/TURN as a **coturn container** in `docker-compose.yml`.
- New tables and endpoints are additive (e.g. `call_attempts`, `call_leg_events`, `user_extensions`, `/calling/*` blueprint). `/cdr*` stays as it is.

### 3.5 Acceptance gates (not done until proven on the real Issabel)

Test with isolated test extensions and the real trunk: register, two-way audio, answer, busy, no answer, hangup from either side, network drop, PBX restart, second tab, and the recording visible via `/cdr/recording`.

## 4. Roadmap and status

| Phase | Scope | Status |
|---|---|---|
| 0 | Foundation: SQL migration runner (`CAWP-API/manage_db.py`, `db/migrations/`), `audit_events`, `idempotency_keys`, `revision` columns + triggers, `core/{audit,idempotency,revisions,riyadh,permissions,api_response}.py`, pytest on a small test DB, root CI, secret defaults removed, sidebar groups, URL-hash list state (clients list) | **Done 2026-09-23**: `review/phase0-backend.md`, `review/phase0-frontend.md` |
| 1 | Core workflow: attention queue, first-class follow-ups, promise lifecycle + evidence ledger, payment reversal + exact-balance approval, reviewed corrections, contact/note history | **Done 2026-09-23**: `docs/phase1-spec.md`, `review/phase1-{money,worklist,history,frontend}.md`; migrations 0007–0015 |
| 2 | Reviewed bulk jobs: review → confirm → per-case results on `core/jobs.py`; assignment + round-robin, archive/restore, lock, notes, corrections, spreadsheet inquiry; jobs screen; retention worker | |
| 3 | Internal requests, targets, repayment plans, session revocation on grant change | |
| 4 | Reports RPT-01…25, formula-safe exports, Arabic XLSX | |
| 5 | Calling (track B): 5a Issabel commissioning (start now), 5b calling backend, 5c in-app softphone, 5d campaigns, 5e CDR attribution | 5a waiting on PBX access |
| 6 | AI and quality review | Needs OpenAI key + retention policy |
| 7 | Load/security tests, UAT, deployment, cutover | |

Conventions set by Phase 0: schema changes only via `db/migrations/NNNN_*.sql` + `manage_db.py up`. New routes use `core/api_response.py` (real HTTP codes); legacy routes keep 201-on-error. New mutating routes use `@idempotent()` and revision checks, and write `audit_events`. New permission keys go in `core/permissions.py`. New list screens use `useHashState`. Revision columns are deliberately not mapped in `models.py`.

Known pre-existing issues (not yet fixed):
- `/dashbord/collector` returns "list index out of range" and `/dashboard` hits an int parse error on a date. Both return 201 errors.
- Frontend lint (824 errors) and type-check (~4.7k) fail, so they are not CI gates.
- Clients-list column headers don't switch language.
- In the collapsed sidebar, sub-items have no icons.
- Notifications "show all" points to a missing route.
- `CAWP-API` has no `.dockerignore`, so `.env` files get copied into the image.
- `tests/smoke.py` defaults to the deleted `aidi.collector` account.
- Recordings SSH uses `AutoAddPolicy`, so the PBX host key is not verified.

Phase 1 decisions and fixes:
- **Reversal on legacy screens:** a reversed payment is also set to `approval_status = false` and `is_deleted = true`, so legacy totals exclude it. Legacy screens therefore show it as rejected or hidden; v2 screens show it as reversed.
- **Legacy `approvePayment`** now locks the case row and compares balances exactly in numeric. Before this, parallel approvals exceeded the debt.
- **Legacy `POST/PUT /roles`** keep the registered new keys. A permission *object* now grants only its `true` keys; before, it granted every key it contained.
- **Password hashes** are no longer serialized. They had leaked from 18 endpoints, including exports. The user edit form treats an empty password as "keep the current one".
- **Legacy note form** sends timed callbacks with the `+03:00` offset. About 7k older date-only callbacks are stored at 00:00 UTC and display as 03:00; a few hundred older timed ones are 3 h off.
- **Case timeline** (`/comments/clint/<id>`) hides notes retired through v2.

Phase 1 open items (need a decision):
- **Permission gates on legacy routes.** These legacy routes still have no permission check: payment approve/reject/edit/delete, `PUT /clints/<id>`, `/clints/delinquency/<id>`, and `DELETE /comments/<id>` (which hard-deletes).
- **Late imported follow-ups:** 2,804 are late, and 2,083 of those cases have a later note. Candidate for a reviewed bulk "no further action" job (Phase 2).
- **Archived cases:** the attention queue leaves them out, which hides 446 archived cases with pending payments.
- **Default contacts:** 127,885 debtors have none. The identity check accepts Saudi 10-digit IDs only.
- **Blocked contacts:** only v2 knows about blocking. Legacy screens can still show and dial a blocked number, so Phase 5 calling must check `is_blocked`.
- **Second corrections reviewer:** only `aidi.dev` holds `case_corrections_review`, so a real approval was not exercised end to end.
- **`audit_events` retention** is not planned yet. It grows by about 0.75 KB per imported contact row.
- **Smaller API gaps** (listed in `review/phase1-frontend.md` §6): payment detail has no events, and the worklist 403 responses have no `code`.
- **Test data:** ZZ-TEST portfolio, cases and role in the Docker test DB. `aidi.collector` is a local-only test collector.
