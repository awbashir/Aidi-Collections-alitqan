# Phase 5 P2: progressive calling campaigns

Stream P2 of `docs/phase5-spec.md`, on branch `phase-5-calling` (not committed). It builds on P0 (`review/phase5-foundation.md`) and P1 (`review/phase5-click-to-call.md`). The real Issabel PBX (10.10.0.220) was **never contacted**; everything live ran against the local test PBX (`pbx-test`) and the `calling` service pointed at the test database `cawp_test_call2`. `cawp-db` was only read, in read-only transactions, for the query timings in section 9. P3's `pbx-webrtc-test` was not touched.

**Progressive only:** one call per ready collector. Nothing is predictive or speculative. A call is authorized only for a member who is ready, has a live lease and has no reservation, and only when a PBX session is connected.

## 1. Summary

| Piece | Where | State |
|---|---|---|
| Data model (campaigns, roster, readiness leases, campaign attempts, pool state) | `db/migrations/0054_campaigns.sql`, `0055_campaign_attempts_pool.sql` | applied by the test harness; guards tested |
| Pool: saved clients-list filter and/or contracts/portfolios, order, exclusions, contact choice | `services/calling/campaign_pool.py` | parity with `build_clients_query`, 14 tests |
| Dispatch: one call per ready member through the **same** `dispatch.authorize()` as `POST /v2/calls` | `services/calling/campaign_dispatch.py`, `services/calling/dispatch.py` (refactor + one claim recheck) | 19 tests, including two concurrent loops |
| The loop in the `calling` service | `calling/main.py`, `calling/config.py` | live |
| Model, lifecycle, roster, heartbeat, next, views | `services/calling/campaign.py` | 8 API tests |
| API (16 routes) | `api/calling_campaigns.py` (**to register**, see section 12) | tested on a test app |
| Live end to end | `tests/test_calling_campaign_live.py` | **4 passed** against the test PBX |
| Tests | `tests/test_calling_campaign_{helpers,pool,dispatch,api,live}.py`, `tests/fixtures/seed_calling_campaign.sql` | **45 passed** (41 + 4 live); whole suite **1105 passed, 2 skipped** |

## 2. Files

New:
- `db/migrations/0054_campaigns.sql`, `db/migrations/0055_campaign_attempts_pool.sql`.
- `CAWP-API/services/calling/campaign.py`, `campaign_pool.py`, `campaign_dispatch.py`.
- `CAWP-API/api/calling_campaigns.py` (blueprint `calling_campaigns`, `URL_PREFIX = None`).
- `CAWP-API/tests/test_calling_campaign_helpers.py` (fixtures, no tests), `test_calling_campaign_pool.py`, `test_calling_campaign_dispatch.py`, `test_calling_campaign_api.py`, `test_calling_campaign_live.py`, `CAWP-API/tests/fixtures/seed_calling_campaign.sql`.

Changed:
- `services/calling/dispatch.py`:
  - `authorize_call()` now parses the body and calls the new `authorize(actor, case_id, contact_id, *, transport, mode, campaign_id, audit_after, audit_kw)`. The body of `authorize()` is the old one: advisory locks, `rules.evaluate`, the transport check after the rules, attempt, reservation, `authorized`, queue row, audit.
  - The click-to-call wire behaviour is identical. The API still turns `RuleRefused` into the same errors, and `transport_not_connected` is still checked after the rules.
  - `claim()` adds one step, **only for `mode = 'campaign'`**: `campaign_dispatch.claim_recheck()`, after the final rules recheck and before the token.
  - All P0/P1/P4 calling tests pass unchanged.
- `calling/main.py`: starts a `CampaignDispatcher` thread next to the click-to-call Dispatcher; adds `campaign_transport()`; the status, heartbeat detail, health (`campaign dispatcher is not running`) and shutdown include it.
- `calling/config.py`: `CALLING_CAMPAIGNS_ENABLED` (default 1; it needs the dispatcher) and `CALLING_CAMPAIGN_POLL_MS` (default 500).
- `services/calling/__init__.py`: the module list (docstring only).

Not touched: `app.py`, `models.py` (every new table is unmapped), `core/permissions.py` (`campaigns` and `campaigns_manage` were already registered), `conftest.py`, `seed.sql`, `api/__init__.py`, `docker-compose.yml`, `pbx-test/**`, P3's files, the frontend.

## 3. Model (0054–0055)

No FK to `clints`/`users` (the legacy code hard-deletes them). Guards use the P0 SQLSTATEs: `CL001` append-only / no delete / no truncate, `CL002` identity or set-once, `CL003` transition.

| Table | Rules |
|---|---|
| `campaigns` | `name`, `status` (`draft`, `running`, `paused`, `draining`, `stopped`), `pool` (jsonb definition, section 4), settings as checked columns (`next_mode` auto\|manual, `wrapup_delay_seconds` 0–600 (default 10), `max_attempts_per_day` 1–50 (3), `cooldown_minutes` 0–10080 (60), `lease_seconds` 10–300 (30)), `created_by`, start/pause/drain/stop times, `stop_reason` (`discarded`, `stopped`, `drained`), `roster_changed_at`, `revision` (`cawp_bump_revision`). The guard enforces the transitions below; start and drain times are set once; a **stopped campaign never changes**; no delete. |
| `campaign_members` | (campaign, user) roster with `active`, who added, who changed and why. Deactivated, never deleted. |
| `campaign_readiness` | Per member: the declared `state` (`ready`, `paused`, `wrap_up`, `offline`), `state_since`, `state_reason` (e.g. `agent_did_not_answer`, `member_removed`), **`lease_until`** (at most 10 min after the heartbeat), `next_requested_at`, `last_dispatch_at`, `last_attempt_id`, and the dispatcher's last `block_reason` with its detail and check time. |
| `campaign_attempts` | Append-only link from `call_attempts` to (campaign, member, case, contact, `contact_choice` default\|fallback, `priority` callback_due\|normal, `dispatched_by`). The trigger checks that the call attempt has `mode = 'campaign'` and the same campaign, collector, case and contact, and that the member is active. |
| `campaign_pool_state` | Per (campaign, case): `last_tried_at`, `last_attempt_id`, `attempts_day`/`attempts_today`, `total_attempts`, and the last refusal by the rules (`excluded_reason`, `excluded_detail`, `recheck_after`). It is a cache and a view. **The rules are always re-evaluated at dispatch.** |

**One outstanding attempt per collector** is enforced by P0's partial unique index on *active* `call_reservations` per collector (and per case, per extension). Every campaign call holds its reservation until the wrap-up, a person's reconciliation, or the evidence-proven `no_customer_contact` release. No new release source was added, so a restart or a timer still never releases a reservation.

Lifecycle:

```
draft    -> running (start) | stopped (stop: stop_reason discarded)
running  -> paused (pause) | draining (drain) | stopped (stop)
paused   -> running (start = resume) | draining | stopped
draining -> stopped (the calling service, once no campaign call is queued, live, uncertain or awaiting wrap-up;
                     or at once when nothing was outstanding; or stop)
stopped  : terminal
```

Pause, drain and stop cancel campaign calls that are still **queued** (never handed to the PBX): the attempt becomes `cancelled` (reason `campaign_paused`, `campaign_draining` or `campaign_stopped`) and the reservation is released `cancel_unsent`. A row the dispatcher is claiming at that moment is skipped (`SKIP LOCKED`); it is being sent and then continues like any live call. **A live call is never cut**: the server cannot hang up (P0 decision 7), and nothing in P2 acts on a call in progress. Its wrap-up still works after a pause or stop.

## 4. The pool

The definition is **evaluated at every dispatch**, never frozen:

```json
{"contract_ids": ["…"], "portfolio_ids": ["…"],
 "filter": {"customeFilterUserValue[]": ["…"], "filterField": "amount", "filterOperator": ">",
            "filterFieldType": "number", "filterValue": "100"}}
```

- `filter` takes the **clients list's own query parameters** (`services/clients_query.ListParams`), so the frontend can save the list's current filter as it is:
  - `customeFilter{Contract,Portfolios,User,CommentStatus,Branches}Value[]` (also `customeFilterStatusesValue`, with `"0"` dropped as the list does);
  - `customeFilterAttributeValue` (a `LIKE` on the JSON text, as the list does);
  - `customeFilterPortfolioCreateAt{,From,To}Value`;
  - the grid filter `filterField`/`filterOperator`/`filterFieldType`/`filterValue`/`filterValue[]` on amount, name, account, identity, portfolio name, contract name and last comment time.
- Paging and sort keys are accepted and ignored.
- These are refused with 422:
  - the spreadsheet filter (a temporary upload, which a campaign cannot keep);
  - unknown keys (a typo must not widen the pool);
  - a grid filter the list itself cannot evaluate, e.g. a number operator on a text column.
- At least one criterion is required.
- **Parity is tested:** for 16 filters, the pool's unarchived cases equal `build_clients_query`'s ids.
- **Scope:**
  - the pool is bounded by the **current** scope of the campaign's creator;
  - a case is offered only to a member whose own scope contains it. This is the click-to-call scope rule, so a collector gets only their own cases; nothing is reassigned.
- Archived cases stay in the pool, excluded as `case_archived`, so the supervisor sees them.

**Order** (deterministic):
1. Priority: `callback_due` before `normal`. `callback_due` means an open follow-up that is callable now: due, late, or within the 15-minute grace.
2. Oldest last contact first: `greatest(last note, last time this campaign tried the case)`, with never-contacted cases first. Any other recent call is covered by the cooldown.
3. Case id.

**Exclusions** (the supervisor view reports the first that applies, in this order):

| Reason | Meaning |
|---|---|
| `case_archived`, `case_locked` | case state |
| `do_not_call` | an active `calling_blocks` row |
| `calling_review_pending` | an open review flag with `blocks_calling` (P1) |
| `in_call` | an active reservation on the case (any collector, any mode) |
| `no_member_in_scope` | no active member may be offered it |
| `case_paid` | outstanding (original minus approved, non-reversed payments) <= 0 |
| `no_callable_contact` | no active, unarchived, undeleted, unblocked contact with a plausible E.164 number |
| `callback_scheduled` | an open follow-up due more than 15 minutes from now |
| `max_attempts_today` | outbound attempts on the case since Riyadh midnight >= `max_attempts_per_day` |
| `cooldown` | an outbound attempt on the case within `cooldown_minutes` |
| `recheck_later` | the rules refused it recently (`campaign_pool_state.recheck_after`) |
| `eligible` | |

**Counting decision:**
- Attempts per day and the cooldown count **every outbound attempt on the case, in any mode and by anyone**: manual logs, click-to-call and every campaign. This protects the debtor.
- An attempt whose reservation was released `no_customer_contact` or `cancel_unsent` is **not** counted: it provably never reached the customer.

**Contact choice** (`campaign_pool.choose_contact`):
1. the debtor's **default** contact when it is callable (active, not archived, not deleted, not blocked, a plausible E.164 number; national `05…` becomes `+9665…`);
2. otherwise the **most recently added** callable contact, then by id;
3. none: `no_callable_contact`.

A number that is not plausibly E.164 is never used or guessed. The choice is stored in `campaign_attempts.contact_choice`, and the rules then check the chosen contact again.

## 5. Dispatch

A dispatch is one transaction, in `campaign_dispatch.dispatch_member()`, which the service loop and the member's "next" share:

1. Take the campaign row `FOR SHARE`, so a pause/drain/stop either waits for the dispatch or the dispatch sees the new status.
2. Pick the member's readiness row `FOR UPDATE SKIP LOCKED`, longest idle first. The member must meet all of these:
   - active member;
   - declared `ready` (or `wrap_up` in auto mode) with a **live lease**;
   - no active reservation;
   - the last campaign call is not a failure of their own extension;
   - the wrap-up delay after the last campaign call's release has passed (unless they clicked "next" since);
   - in manual mode, an unexpired "next" (120 s);
   - not blocked in the last 5 s (backoff).
3. Run the member-level rules once (`calls_make`, active, extension, calling window) and the transport check. A failure blocks the member with that reason.
4. Take the candidates in pool order (section 4). For each:
   - `pg_try_advisory_xact_lock` on the case, with the same key `dispatch.authorize()` uses (another loop has it: skip);
   - choose the contact;
   - then, in a **savepoint**, **`dispatch.authorize()`: exactly the code of `POST /v2/calls`**. It runs every click-to-call rule (scope of that collector, do-not-call, blocked contact, archived/locked, closed/paid, future callback, calling window, reservations, review flags), then writes the attempt (`mode = 'campaign'`, `campaign_id`), the reservation, the dispatch queue row and the audit `call.dispatch.authorize` (with `campaign_id`, `contact_choice`, `priority`, `dispatched_by`).
   - A case-level refusal is remembered in `campaign_pool_state` with a re-check time (e.g. 30 min for `case_paid`, until `callable_from` for a callback) and the next case is tried. A member-level refusal blocks the member.
5. Write `campaign_attempts`, the pool state, the readiness (`last_dispatch_at`, "next" consumed, `wrap_up` back to `ready`) and the audit `campaign.dispatch`.

The click-to-call Dispatcher then claims the queue row as for any click-to-call:
- the final rules recheck;
- **`claim_recheck()`**: campaign still `running`, member active and ready with a live lease. Otherwise the row is refused `campaign_paused`/`campaign_draining`/`campaign_stopped`/`member_inactive`/`member_not_ready`/`lease_expired`, the attempt fails, and the reservation is released `no_customer_contact`;
- token, one Originate, the AGI gate, evidence, projection, reconciliation.

All of that is unchanged P1 code.

**Where it runs: the `calling` service, not the worker.**
- It is the only AMI owner, so it knows at once whether a session is connected and which PBX epoch the call would go to. `campaign_transport()` returns nothing while it is disconnected or the dispatcher thread is down, so no campaign call is authorized.
- The click-to-call Dispatcher in the same process claims the row within 0.25 s.
- Readiness leases are seconds-long, which the worker's job leases are not built for.
- The loop polls every 0.5 s. Per running or draining campaign it does housekeeping (auto-pause, drain completion), then dispatches until no member is eligible.

**Restart safety:**
- The loop keeps nothing that matters in memory.
- A crash before COMMIT leaves nothing (tested).
- After COMMIT the queue row is claimed or expires like any click-to-call.
- The P0/P1 startup recovery marks in-flight attempts `uncertain` and **keeps their reservations**, so those members get no new call until a person or the evidence resolves them. Campaigns keep their status and other members continue (tested).
- Several processes are safe: the readiness row lock, the case try-lock and the unique reservation indexes (tested with two loops, and with the member lock bypassed).

**Auto-pause:** a member whose last campaign call failed because **their extension** did not answer (`agent_no_answer`, `agent_busy`, `agent_rejected`, `agent_unavailable`) is set to `paused` (`state_reason agent_did_not_answer`). An unavailable extension is not an available collector (live-tested with 7203). They go back with a heartbeat `ready` or "next".

**Fence (test isolation, also an operator tool):**
- Every service-loop pass over a campaign takes `pg_try_advisory_xact_lock_shared('campaign_dispatch:<id>')`. Whoever holds that lock exclusively keeps every calling-service loop off that campaign without blocking anything.
- The test suite fences its non-live campaigns, so a calling service running on the same database (needed for the live tests) never touches them.
- A member's own "next" is not fenced. To stop dispatch for people, pause the campaign.

## 6. Readiness and member states

`POST /v2/campaigns/<id>/heartbeat {"state": "ready"}`:
- sets the declared state and renews the lease (`lease_seconds`);
- `{}` only renews it; the first heartbeat needs a state;
- `wrap-up` is accepted as an alias of `wrap_up`;
- `offline` ends the lease at once;
- a state change is audited (`campaign.readiness.<state>`); a plain renewal is not;
- being `ready` in two campaigns at once is refused (`409 ready_in_other_campaign`, with the other campaign's id).

The **effective state** that the views show is derived:

| state | reason(s) |
|---|---|
| `removed` | `member_inactive` |
| `on_call` | (live call), `call_uncertain` |
| `wrap_up` | `wrapup_pending` (the call ended, waiting for its wrap-up), `wrapup_delay` (+ `ready_at`) |
| `offline` | declared, never heartbeated, `lease_expired`, `member_removed` |
| `paused` | declared, `agent_did_not_answer` |
| `waiting_next` | manual mode, no pending "next" |
| `ready` | `null` (dispatching), or why not: `campaign_paused`, `campaign_draining`, `campaign_draft`, `campaign_stopped`, `transport_not_connected`, `calling_window_closed`, `calling_windows_missing`, `calling_holiday`, `no_extension`, `forbidden`, `actor_inactive`, `calling_reserved`, `no_eligible_case` |

**Auto mode:** after the wrap-up (the reservation is released) the member is called again once `wrapup_delay_seconds` has passed. A member who declared `wrap_up` is treated as ready after the delay. "Next" skips the rest of the delay.

**Manual mode:** a call is placed only after the member's "next" (valid 120 s). "Next" tries to dispatch **at once** in the API request through the same `dispatch_member()` and returns the result. A "next" during a call or an unsaved wrap-up gets `409 call_in_progress`.

## 7. API

To register: add `"api.calling_campaigns"` to `api/__init__.py` `MODULES`. Conventions are those of `core/api_response.py`:
- mutations need `Idempotency-Key`, except the heartbeat, where it is optional (the route commits itself without a key);
- campaign edits, transitions and roster changes need `expected_revision` (body) or `If-Match` (the campaign's revision);
- every write is audited;
- an invisible campaign answers 404.

| Route | Key | Notes |
|---|---|---|
| `GET /v2/campaigns?status=running,paused&mine=1&page=&page_size=` | `campaigns` \| `campaigns_manage` | each item has `my_membership`, `my_readiness` |
| `POST /v2/campaigns` | `campaigns_manage` | `{name, pool, settings?, member_ids?}` -> 201 draft |
| `GET /v2/campaigns/<id>` | `campaigns` \| `campaigns_manage` | |
| `PATCH /v2/campaigns/<id>` | `campaigns_manage` | `{expected_revision, name?, pool?, settings?}`; any status but stopped |
| `POST /v2/campaigns/<id>/start` \| `pause` \| `drain` \| `stop` | `campaigns_manage` | `{expected_revision, reason?}`; `start` also resumes a paused campaign; response adds `cancelled_queued`, `drained` |
| `GET /v2/campaigns/<id>/members` | `campaigns_manage` | roster with extension, who/when/why |
| `POST /v2/campaigns/<id>/members` | `campaigns_manage` | `{expected_revision, user_ids, reason?}` -> `added`, `reactivated`, `revision` |
| `POST /v2/campaigns/<id>/members/<user_id>/deactivate` | `campaigns_manage` | `{expected_revision, reason}`; the member's lease ends; a live call continues |
| `GET /v2/campaigns/<id>/me` | `campaigns` \| `campaigns_manage` | my status (member only) |
| `POST /v2/campaigns/<id>/heartbeat` | `campaigns` \| `campaigns_manage` | `{state?}` -> my status |
| `POST /v2/campaigns/<id>/next` | `campaigns` \| `campaigns_manage` | -> my status + `dispatch` |
| `GET /v2/campaigns/<id>/live` | `campaigns_manage` | supervisor live view |
| `GET /v2/campaigns/<id>/pool?reason=cooldown,case_paid&page=` | `campaigns_manage` | the pool in dispatch order with each case's reason + summary |

**Visibility:**
- A manager sees campaigns they created, or created by or with members in their team (supervised users). SuperAdmin sees everything.
- A member sees campaigns they are on the roster of.
- Members must be active users the manager may see (`422 member_not_allowed`).

**Refusals:**
- `409 invalid_transition` (`{status, action}`), `no_members`, `campaign_stopped`, `campaign_not_running`, `member_inactive`, `ready_in_other_campaign`, `call_in_progress`, `revision_conflict`;
- `422 validation_failed` with the field (e.g. `{"settings.dial_ratio": "unknown"}`: there is no dial ratio, progressive only), `member_not_allowed`;
- `403 forbidden`; `404 not_found`.

Examples below come from the running code (ids shortened, repeated campaign blocks shortened to `…`).

**Create**: `POST /v2/campaigns`

```json
{"name": "September arrears - team A",
 "pool": {"contract_ids": ["f7cb…1f"], "filter": {"filterField": "amount", "filterOperator": ">",
          "filterFieldType": "number", "filterValue": "100"}},
 "member_ids": ["a732…22", "6d1c…bf"],
 "settings": {"next_mode": "auto", "wrapup_delay_seconds": 10, "max_attempts_per_day": 3,
              "cooldown_minutes": 60, "lease_seconds": 30}}
```
-> `201`
```json
{"message": "campaign created", "status": true, "data": {
  "id": "a04a…55", "name": "September arrears - team A", "status": "draft",
  "pool": {"contract_ids": ["f7cb…1f"], "portfolio_ids": [], "filter": {"filterField": "amount", "filterValue": "100",
           "filterOperator": ">", "filterFieldType": "number"}},
  "settings": {"next_mode": "auto", "wrapup_delay_seconds": 10, "max_attempts_per_day": 3, "cooldown_minutes": 60,
               "lease_seconds": 30},
  "created_by": {"id": "00b8…5a", "name": "CL CP Supervisor"}, "created_at": "2026-09-24T08:29:43.518019+00:00",
  "updated_at": "…", "started_at": null, "paused_at": null, "drain_requested_at": null, "stopped_at": null,
  "stop_reason": null, "members": {"active": 2, "total": 2}, "revision": 0}}
```

**Start**: `POST /v2/campaigns/<id>/start {"expected_revision": 0}` -> `200`, the campaign with `"status": "running"`, `"started_at": "…"`, `"revision": 1`, `"cancelled_queued": []`, `"drained": false`.

**Heartbeat / my status**: `POST /v2/campaigns/<id>/heartbeat {"state": "ready"}` (and `GET …/me`) -> `200`
```json
{"campaign": {"id": "a04a…55", "status": "running", "…": "…"},
 "member": {"user": {"id": "a732…22", "name": "CL CP One"}, "active": true, "extension": "62070",
            "state": "on_call", "reason": null, "ready_at": null,
            "declared": {"state": "ready", "since": "…43.569Z", "reason": null, "lease_until": "…30:13.569Z",
                         "lease_live": true, "heartbeat_at": "…43.569Z"},
            "next_requested_at": null, "last_dispatch_at": "…43.627Z", "blocked": null,
            "current_attempt": {"id": "6e73…88", "state": "authorized", "reason_code": null, "mode": "campaign",
                                "campaign_id": "a04a…55", "this_campaign": true,
                                "case": {"id": "48aa…50", "name": "CL case", "account_number": "ACC-…"},
                                "authorized_at": "…", "bridged_at": null, "ended_at": null},
            "today": {"attempts": 1, "conversations": 0, "customer_answered": 0, "wrapped_up": 0, "failed": 0,
                      "cancelled": 0, "uncertain": 0, "live": 1, "outcomes": {}}},
 "state": "on_call", "reason": null, "ready_at": null,
 "next": {"mode": "auto", "pending": false, "requested_at": null, "expires_at": null},
 "attempt": {"…": "the P1 live view of the call (GET /v2/calls/<id>/live): dispatch, timeline, conversation, actions.can_wrapup, wrapup_outcomes, revision, poll_after_ms"},
 "heartbeat_after_ms": 10000, "poll_after_ms": 1000}
```

After the wrap-up, in the delay: `"state": "wrap_up", "reason": "wrapup_delay", "ready_at": "2026-09-24T08:29:53.689934+00:00", "attempt": null, "poll_after_ms": 3000`, and `today.outcomes` becomes `{"connected": 1}`. The frontend should:
- heartbeat every `heartbeat_after_ms` (a third of the lease);
- poll `me` every `poll_after_ms`;
- wrap up with P0's `POST /v2/calls/<id>/wrapup`, using `attempt.revision`.

**Next**: `POST /v2/campaigns/<id>/next {}` -> `200`, my status with `"state": "on_call"` and `"dispatch": {"attempt_id": "c5d2…ab", "case_id": "a171…1b", "contact_choice": "default", "priority": "normal"}`. When nothing can be called, `"dispatch": {"blocked": "no_eligible_case", "detail": {"candidates_checked": 0}}` (or the member-level reason), and `"dispatch": null` when the member is not eligible (e.g. the lease or the mode).

**Supervisor live view**: `GET /v2/campaigns/<id>/live` -> `200`
```json
{"campaign": {"…": "…"},
 "transport": {"connected": true, "epoch": "a7a2…@2026-09-24T05:00:00"},
 "members": [
   {"user": {"id": "a732…22", "name": "CL CP One"}, "active": true, "extension": "62070", "state": "on_call",
    "reason": null, "ready_at": null, "declared": {"state": "ready", "lease_live": true, "…": "…"},
    "current_attempt": {"id": "6e73…88", "state": "bridged", "case": {"…": "…"}, "bridged_at": "…", "this_campaign": true},
    "today": {"attempts": 1, "conversations": 1, "customer_answered": 1, "wrapped_up": 0, "live": 1, "outcomes": {}, "…": 0}},
   {"user": {"id": "6d1c…bf", "name": "CL CP Two"}, "state": "offline", "reason": null, "declared": null,
    "current_attempt": null, "today": {"attempts": 0, "…": 0}}],
 "members_by_state": {"on_call": 1, "offline": 1},
 "pool": {"size": 3, "eligible_now": 2, "remaining_today": 3, "excluded": {"in_call": 1},
          "evaluated_at": "2026-09-24T08:29:43.672168+00:00", "cached": false},
 "today": {"attempts": 1, "conversations": 1, "customer_answered": 1, "wrapped_up": 0, "failed": 0, "cancelled": 0,
           "uncertain": 0, "live": 1, "outcomes": {}},
 "all_time": {"attempts": 1, "uncertain": 0, "awaiting_wrapup": 0},
 "outstanding_calls": 1, "poll_after_ms": 2000}
```
- `conversations` counts bridge-evidenced calls; `customer_answered` counts customer-leg answers (never a conversation by itself).
- `remaining_today` is the pool size minus the exclusions that do not clear by themselves today (archived, locked, do-not-call, review, no member in scope, paid, no contact, max attempts).
- The pool summary is cached per process for 15 s (`cached`, `evaluated_at`); see section 9.

**Pool preview**: `GET /v2/campaigns/<id>/pool?page_size=5` -> `{items: [{case {id, name, account_number, owner_id}, reason, priority, last_contact_at, callback_at, attempts_today, last_tried_at, last_excluded_reason}], total, page, page_size, summary}`, eligible cases first, each group in dispatch order. `?reason=cooldown,case_paid` filters.

**Pause**: `{"expected_revision": 1, "reason": "team meeting"}` -> `"status": "paused", "cancelled_queued": ["c5d2…ab"]` (a call that was queued and not yet sent). **Drain** with nothing outstanding -> `"status": "stopped", "stop_reason": "drained", "drained": true`; with calls outstanding -> `"status": "draining"`. **Deactivate member** `{"expected_revision": 2, "reason": "moved to team B"}` -> the roster with `"active": false, "changed_by": {…}, "change_reason": "moved to team B"`, plus `"deactivated"` and `"revision": 3`.

Audit actions:
- `campaign.create`, `campaign.update` (before/after), `campaign.start`, `campaign.resume`, `campaign.pause`, `campaign.drain`, `campaign.stop`, `campaign.drained`;
- `campaign.members.add`, `campaign.members.deactivate`;
- `campaign.readiness.<state>`, `campaign.readiness.auto_paused`, `campaign.next`;
- `campaign.dispatch` (source `campaign` from the service, `api` from "next");
- plus P1's `call.dispatch.authorize` (with `campaign_id`) and `call.cancel`.

## 8. Tests and results

```
docker compose --profile pbx-test up -d pbx-test
CALLING_ENABLED=1 CALLING_RECONCILE_SECONDS=3 \
CALLING_DATABASE_URL=postgresql+psycopg2://postgres:postgres@test-db:5432/cawp_test_call2 \
    docker compose --profile pbx-test up -d calling
docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call2 api-test tests/test_calling_campaign*.py
```

| File | Tests | What |
|---|---|---|
| `test_calling_campaign_pool.py` | 14 | pool definition validation (9 cases) and normalisation; **parity with `build_clients_query` for 16 saved filters**; selection order (callback due, never contacted by id, oldest contact, recently tried last) in the dispatcher and the preview; **every exclusion** reported once and never offered; contact choice (default, blocked default -> newest callable, inactive/archived skipped, invalid never guessed, national form) |
| `test_calling_campaign_dispatch.py` | 19 | the same path as click-to-call (attempt, reservation, queue row with the epoch, audits, pool state, readiness); one outstanding attempt per collector and fair order; **two concurrent loops** (exactly one call per collector, never the same case twice); **the reservation index holds even with the member lock bypassed**; pause (queued cancelled `cancel_unsent`, live call untouched, wrap-up works, resume); drain (waits for the call and its wrap-up, then `drained`); drain with nothing outstanding and stop (live call kept); **an expired lease never cuts a live call** and no new call until the lease is renewed; going offline keeps the live call; the claim recheck (lease expired, campaign paused, then a real claim); **restart safety** (a crash before COMMIT leaves nothing; startup recovery keeps the reservation, the campaign keeps running, no duplicate); the fence; max attempts per day (not-reached-customer attempts and yesterday not counted); cooldown; wrap-up delay then auto next, "next" skips it; manual mode; auto-pause of an absent agent; member-level blocks with backoff; a case paid in a race is remembered and the next case taken |
| `test_calling_campaign_api.py` | 8 | create (validation, progressive only, members of the team only, idempotent replay, audit); visibility and permissions; update with revision and audit; lifecycle transitions and the audit trail; roster add/reactivate/deactivate (reason, revision, live call kept); heartbeat (first needs a state, alias, lease renewal, audit on change only, ready in another campaign, stopped); the supervisor live view; the 0054/0055 database guards |
| `test_calling_campaign_live.py` | 4 | **live, through the real calling service** (section 9) |

Results:
- Non-live campaign tests: **41 passed** in about 8 s.
- Live campaign tests: **4 passed** in 111 s.
- All calling tests with the services down: **309 passed, 21 skipped** (the live files).
- **Whole suite with the test PBX and the calling service up on `cawp_test_call2`: 1105 passed, 2 skipped, 0 failed** (7 min 26 s). This includes the P0 and P1 live tests, which ran against the refactored dispatch path and the running campaign loop. The two skips:
  - the host-only dialplan file check (`pbx-test/` is not mounted; untouched by P2);
  - P3's WebRTC live test (it needs its own PBX reading this database).

## 9. Live results and timings

Live, against the test PBX, with the real `calling` service:

1. **Two members, answer / busy / no answer, wrap-ups, auto next.**
   - Setup: member A (7201, answers) has cases ending 4, 2, 3; member B (7204, answers after 3 s) has 4, 2. Delay 3 s.
   - Result: 5 calls in 31.6 s. Each case was called once, in pool order.
     - `…4`: bridged, `customer_hung_up`, wrapped `connected`.
     - `…2`: `customer_busy`, wrapped `busy`.
     - `…3`: `customer_no_answer` after the 20 s ring, wrapped `no_answer`.
   - Never more than one active reservation per member at any poll.
   - **Wrap-up to the next call: 3.02, 3.10, 3.06 s** (delay 3 s, database clock).
   - Live view afterwards: `outcomes {"connected": 2, "busy": 2, "no_answer": 1}`, pool `{"cooldown": 5}`.
2. **Pause, resume, drain.**
   - A 20 s call (`…6`) was bridged and the campaign paused. The call **continued** and ended by evidence (`customer_hung_up`, at least 15 s).
   - After its wrap-up there was no new call for delay + 3 s (`ready` / `campaign_paused`).
   - Resume: the next case was dialled.
   - Drain before that call's wrap-up: `draining`. After the wrap-up the service stopped the campaign (`drained`) within about 4 s. The third case was never dialled.
3. **Manual next:** no call for 3 s without "next"; each "next" dispatched exactly one call.
4. **Absent agent (7203 never answers):** one attempt, `failed` `agent_no_answer`, released `no_customer_contact`, the member auto-paused (`agent_did_not_answer`), no second call.

Query timings on the full local copy (`cawp-db`, read-only; the calling tables do not exist there, so those parts are left out):
- **Candidate query:** 1.24 s for a collector with 64k cases in one contract in a single pass. The shipped two-stage query takes **330 ms**: a cheap ordered window of 200, then the expensive checks on that window. For ordinary collectors it is milliseconds.
- **Pool summary** (classifying every case) for a 75k-case contract: **4.7 s**. The live view therefore caches it for 15 s per process. `GET …/pool` computes it on demand.

## 10. Decisions (for PROJECT-NOTES)

1. **The dispatcher runs in the `calling` service** (the AMI owner), not the worker (section 5). `CALLING_CAMPAIGNS_ENABLED=1` by default; it needs `CALLING_DISPATCH_ENABLED=1`.
2. **One dispatch path:** campaign calls use `dispatch.authorize()` (the `POST /v2/calls` code) plus one campaign recheck at the claim.
3. **The pool is a saved clients-list filter and/or contracts/portfolios**, evaluated at dispatch, bounded by the creator's current scope. A member only gets cases in their own scope; there is no reassignment.
4. **Priority** = a callable due or late callback, then oldest contact (note or campaign try), then id.
5. **Max attempts and cooldown count every outbound attempt on the case, in any mode**, except the ones that provably never reached the customer.
6. **Contact:** the default, else the newest callable contact; nothing is guessed.
7. **Auto-pause** of a member whose extension did not answer.
8. **Heartbeat** without a state = lease renewal; `wrap-up` accepted as `wrap_up`; ready in only one campaign at a time.
9. **Start resumes** a paused campaign; drain with nothing outstanding stops at once; pause/drain/stop cancel only never-sent calls.
10. **No simulated mode:** the test PBX's loopback customers are the simulation (hard rule 8). There is no "simulated" flag against real numbers.

## 11. Containers

`cawp-pbx-test` and `cawp-calling` (`CALLING_ENABLED=1`, database `cawp_test_call2`) were started for the runs and stopped afterwards; start them with the commands in section 8. `cawp-pbx-webrtc-test` (P3) was not touched. No migration was applied to `cawp-db`, and `cawp-api`/`cawp-worker` were not restarted.

## 12. Open items

1. **Integration for the lead:**
   - register `api.calling_campaigns` in `api/__init__.py`;
   - add 0054–0055 to the `db/README.md` migration list;
   - `CAWP-API/DEPLOY.md` §5d should mention `CALLING_CAMPAIGNS_ENABLED` and `CALLING_CAMPAIGN_POLL_MS`;
   - record the decisions in PROJECT-NOTES.
2. **Frontend:** the collector's campaign panel (heartbeat, ready/pause, next, the live call from P1's view, the wrap-up form) and the supervisor screens (create/edit with the clients-list filter, roster, live view, pool preview) are API-only here. P3's softphone presence should drive the heartbeat state.
3. **The pool summary at scale** takes seconds on 75k-case contracts; it is cached 15 s per API process. A materialized per-campaign summary refreshed by the loop would make it cheap if supervisors watch very large pools.
4. **The "callback due" priority** puts every case with a late imported follow-up first (most live cases have one, per Phase 1). This is correct by the rule, but the business may want a finer priority (e.g. promise due, balance). It is one SQL expression (`_PRIORITY`).
5. **Server-side hang-up** stays impossible (P0 decision 7). Stop and pause never end a live call; the PBX limit (600 s) bounds it.
6. **A call being claimed at the instant of a pause is still sent** (it is already in the dispatcher's hands). It then behaves as a live call. This is documented, not a defect.
7. **The campaign's creator is its scope owner:** if the creator is deactivated or loses scope, the pool shrinks accordingly. A "transfer ownership" action is not built.
8. **Retention:** `campaign_pool_state` and readiness rows of stopped campaigns are kept. There is no purge yet (same as P1's open item for `call_events`).
