# Phase 5 P1: click-to-call

Stream P1 of `docs/phase5-spec.md`, on branch `phase-5-calling` (not committed). It builds on the P0 foundation (`review/phase5-foundation.md`). The real Issabel PBX (10.10.0.220) was **never contacted**. Everything was proven against the local test PBX in Docker.

## 1. Summary

| Piece | Where | State |
|---|---|---|
| Dispatch rules (API, dispatcher final recheck, AGI gate recheck) | `services/calling/rules.py` | 15 rule tests, 7 final-recheck tests, 6 gate tests |
| `POST /v2/calls` (authorize + reserve + enqueue) and cancel | `services/calling/dispatch.py`, `api/calling.py` | tested through the API |
| Dispatcher in the `calling` service (claim `SKIP LOCKED`, token, one Originate) | `services/calling/dispatch.py` `Dispatcher`, `calling/main.py` | fake AMI session + live |
| Evidence -> attempt state projection | `services/calling/projection.py`, hooked into `services/calling/evidence.py` | recorded AMI sequences + live |
| Uncertainty and reconciliation (inventory + CDR) | `services/calling/reconcile.py`, `services/calling/cdr_lookup.py` | fake sessions + live crash test |
| Live-call view, my active attempt, supervisor lists | `services/calling/live.py`, `services/calling/review.py` | API tests |
| Locked/archived wrap-up decision | `services/calling/wrapup.py`, migration 0053 | API tests + DB guards |
| Data model | `db/migrations/0052_click_to_call.sql`, `0053_call_wrapup_review.sql` | applied by the test harness |
| Test PBX: agent 7204 (answers after 3 s); supervised Asterisk (crash restart with a new epoch); test-only control port | `pbx-test/` | used by the live tests |
| Tests | `tests/test_calling_c2c_*.py` (83 tests), 3 recorded AMI fixtures | **whole suite 1040 passed, 1 skipped, 0 failed** |

## 2. Files

New:
- `db/migrations/0052_click_to_call.sql`, `db/migrations/0053_call_wrapup_review.sql`.
- `CAWP-API/services/calling/{rules,dispatch,projection,reconcile,cdr_lookup,live,review}.py`.
- `CAWP-API/tests/test_calling_c2c_{helpers,rules,dispatch,projection,reconcile,api,agi,live}.py`, `CAWP-API/tests/fixtures/ami/{answered_customer_hangs_up,busy_customer,agent_no_answer}.json` (recorded from the test PBX; synthetic parties only, caller-ID fields dropped).
- `pbx-test/control.sh`.

Changed:
- `services/calling/evidence.py` (projection after each stored event), `agi.py` (gate rechecks), `wrapup.py` (the locked/archived decision), `queries.py` (wrap-up review fields, `wrapup_pending`, `visible_or_404`, `my_active_attempt_id`, `call_detail(with_evidence=)`), `__init__.py` (module list).
- `api/calling.py`: 8 new routes. `/v2/me/calling` now reports `can_call: true` when the collector has an extension, the window is open, the calling service is connected and the caller holds `calls_make`; it also returns `active_attempt_id`.
- `calling/main.py` (dispatcher and reconciler threads, heartbeat row, startup recovery of `sending` rows, status on session end, health), `calling/config.py` (new variables).
- `pbx-test/`: `entrypoint.sh` (supervises Asterisk, a new epoch before every process start, control port), `Dockerfile`, `conf/pjsip.conf.in` + `conf/extensions_pbx_test.conf` (7204). The generated dialplan is unchanged (the host check equals the generator's output).
- `docker-compose.yml`: `pbx-test` (`PBX_TEST_CONTROL_KEY`), `calling` (dispatcher/reconcile/CDR variables, the CDR volume mounted read-only).
- `docs/issabel-commissioning.md`: `userfield` is required (Issabel `uniqueid` is `varchar(32)`); P1 settings; gates 10, 13, 16, 17; test-PBX differences.
- P0 test updated where my change required it: `tests/test_calling_service.py::test_consumer_session_events_and_legs` (an ungated bridge now projects to `ended`/`not_bridged` instead of staying `dispatched`).

Not touched: `app.py`, `models.py`, `core/permissions.py`, `conftest.py`, `seed.sql`, the frontend, P4's files (I import `attempt_tag`/`resolve_attempt_tags` from `services/calling_cdr.py` without editing it).

## 3. Dispatch rules

`rules.evaluate()` runs **three times** per click-to-call: in the API, in the calling service when it claims the row (the final recheck, seconds later, right before the token and the Originate), and a subset in the FastAGI gate after the agent answered, right before the customer is dialled. The first failing rule is reported.

| Rule code | HTTP | Rule |
|---|---|---|
| `forbidden` / `actor_inactive` | 403 | the collector holds `calls_make` and is active (the service reads the role afresh) |
| `no_extension` | 409 | an active `user_extensions` row; the legacy `users.extension` alone never authorizes; at the claim the extension must be unchanged |
| `not_found` | 404 | the case is in the collector's scope (Phase 1 rules, uncached in the service) |
| `case_archived`, `case_locked` | 409 | neither archived nor locked |
| `do_not_call` | 409 | no active case calling block |
| `calling_review_pending` | 409 | no open review flag that blocks calling (a do-not-call or wrong number deferred on a locked case, section 8) |
| `contact_not_found` (404), `contact_deleted`, `contact_blocked` (409) | | the contact belongs to the case's debtor, is not deleted, not `is_blocked` |
| `destination_invalid` | 422 | the contact value is a plausible E.164 number (national `05…` becomes `+9665…`; never guessed otherwise). The browser never sends a number: an unknown `destination` field is refused |
| `case_paid` | 409 | **closed/paid = outstanding <= 0**, where outstanding = original debt minus approved, non-reversed payments (`services/money.py`); pending and rejected money never count |
| `callback_scheduled` | 409 | **decision:** the case's open follow-up (callback) due more than **15 minutes** from now forbids calling; from 15 minutes before its due time, and for late callbacks, calling is allowed. A date-only callback is due 09:00 Riyadh. The error returns `due_at` and `callable_from` |
| `calling_windows_missing`, `calling_window_closed`, `calling_holiday` | 409 | the Riyadh window, on **database time** |
| `calling_reserved` | 409 | no active reservation for the collector, the case or the extension (the unique indexes also catch races) |
| `transport_not_connected` | 503 | (API) a calling service reported a connected AMI session and a running dispatcher within 15 s (`calling_service_status`) |

The final recheck adds `destination_changed` (the contact's number changed since the API) and refuses a row enqueued for another PBX epoch. The gate recheck covers `case_locked`, `case_archived`, `do_not_call`, `calling_review_pending`, `contact_blocked`/`contact_not_found`; the window at that point is the PBX dialplan's `GotoIfTime` plus the claim-time check. Queue presence never authorizes: nothing reads the attention queue (tested).

## 4. States and the handoff

```
POST /v2/calls   rules -> attempt prepared -> authorized (+ reservation, + call_dispatch_queue row: expected epoch,
                 context, ring 30 s, limit 600 s, expires in 30 s) -> audit call.dispatch.authorize        [API]
Dispatcher       SELECT ... FOR UPDATE SKIP LOCKED the oldest queued row FOR THIS EPOCH
                 -> final recheck -> issue token (SHA-256 stored; plaintext only in memory; bound to the epoch UUID,
                    extension, destination, context, channel id cawp-<attempt>; TTL ring + 30 s) -> bind channel+epoch
                 -> queue `sending` -> COMMIT -> ONE Originate (PJSIP/<ext>, the restricted context, the E.164 exten)
                    Success -> queue `submitted`, attempt `dispatched` (submitted, never "connected")
                    Error   -> queue `rejected`, attempt `failed` originate_rejected, reservation released
                    no/lost answer -> queue `unacknowledged`, attempt `uncertain` originate_unacknowledged (kept)
                 never sent: refused (a rule; attempt failed <rule>), expired (dispatch_expired / pbx_epoch_changed),
                 cancelled (the collector, before the claim)                                    [calling service]
PBX evidence     dispatched -> agent_ringing -> agent_answered -> customer_dialing -> customer_answered -> bridged
                 -> ended | failed | uncertain                                                   [projection]
Reconciliation   uncertain -> ended | failed, or kept for a person                               [reconciler / supervisor]
Wrap-up          ended/failed -> wrapped up; releases the reservation
```

AMI stays in one process: the API only writes the queue row; only the `calling` service holds the AMI session. The token is issued by the service at the claim (not by the API) so its plaintext never leaves the process that sends it and it is bound to the epoch of the session that actually sends; the API binds the row to the epoch it saw, and the service never sends a row of another epoch.

**Reservation releases** (the database guards each source): `wrapup`, `cancel_unsent`, `reconciled` (a person with a reason), and new in 0052 **`no_customer_contact`**: the attempt is `failed` or `cancelled`, no AGI `allow` was ever recorded for it and no customer leg or customer dialing exists. Because the restricted dialplan dials only after an allow is committed in the database, this proves nobody was called. It covers an agent who did not answer or was busy, a refused or rejected dispatch, and reconciliation of a call lost before the gate (once its token expired). There is still no timeout or restart source: a restart never releases a reservation by itself.

## 5. Endpoints

Conventions as P0 (`core/api_response.py`): mutations need `Idempotency-Key`; row edits need `expected_revision`; every write is audited; out-of-scope ids answer 404. Examples below are from a live run against the test PBX (ids shortened).

**Place a call** `POST /v2/calls` (key `calls_make`):

```json
{"case_id": "ec02…f2", "contact_id": "203a…e9"}
```

`201` (the live view, section 6):

```json
{"message": "call authorized; the calling service dispatches it", "status": true, "data": {
  "id": "9ede…76", "mode": "click_to_call", "state": "authorized", "extension": "7201",
  "destination": "+966585145056", "contact": {"id": "203a…e9", "value": "966585145056"},
  "timeline": {"authorized_at": "…06:56:10.649Z", "dispatched_at": null, "…": null},
  "reservation": {"id": "7d02…d5", "active": true, "release_source": null},
  "dispatch": {"state": "queued", "expires_at": "…06:56:40Z", "agent_ring_seconds": 30, "max_call_seconds": 600,
               "ami_response": null, "note": "submitted = the PBX accepted the Originate: submitted, not connected"},
  "live": true, "conversation": {"evidenced": false, "customer_answered": false, "rule": "…"},
  "uncertain": null, "flags": [], "reconciliations": [],
  "actions": {"can_cancel": true, "can_wrapup": false, "wrapup_outcomes": [], "can_resolve": false},
  "events": [], "poll_after_ms": 1000, "etag": "W/\"1.0.0.0.0-0.queued.True\"", "revision": 1}}
```

Refusal example: `409 {"message": "the case is locked; it cannot be called", "status": false, "error": {"rule": "case_locked"}, "code": "case_locked"}`. `callback_scheduled` adds `follow_up_id`, `due_at`, `callable_from`; `calling_reserved` adds `reserved` (collector | case | extension); window refusals add the Riyadh `window`.

**Live view** `GET /v2/calls/<id>/live` (`calls` | `calls_make`), polled every `poll_after_ms`; send `If-None-Match: <etag>` to get `304` with no body when nothing changed. While bridged:

```json
{"state": "bridged", "pbx": {"epoch": "2f8f…@2026-09-24T06:56:08", "channel_id": "cawp-9ede…76"},
 "dispatch": {"state": "submitted", "ami_response": "Success", "claimed_at": "…10.713Z", "finished_at": "…10.719Z"},
 "timeline": {"dispatched_at": "…10.719Z", "agent_answered_at": "…10.721Z", "customer_dialing_at": "…10.727Z",
              "customer_answered_at": "…13.733Z", "bridged_at": "…13.743Z", "ended_at": null},
 "conversation": {"evidenced": true, "customer_answered": true},
 "events": [
   {"at_pbx": "1790232970.719545", "source": "pbx", "kind": "agent_channel_created", "leg": "agent"},
   {"at_pbx": "1790232970.721145", "source": "pbx", "kind": "agent_answered", "leg": "agent"},
   {"at_pbx": "1790232970.721204", "source": "pbx", "kind": "originate_response", "detail": {"response": "Success"}},
   {"at": "…10.723Z", "source": "agi", "kind": "agi_allow", "detail": {"reason": "allowed"}},
   {"kind": "dialplan_authorized"}, {"kind": "customer_dial_started"}, {"kind": "customer_ringing", "leg": "customer"},
   {"kind": "customer_answered", "leg": "customer"}, {"kind": "customer_dial_ended", "detail": {"dial_status": "ANSWER"}},
   {"kind": "bridge_enter", "leg": "customer", "detail": {"bridge": "6b3e…0e", "members": "1"}},
   {"kind": "bridge_enter", "leg": "agent", "detail": {"bridge": "6b3e…0e", "members": "2"}}],
 "actions": {"can_cancel": false, "can_wrapup": false}, "poll_after_ms": 1000}
```

After the customer hung up (20 s later):

```json
{"state": "ended", "reason_code": "customer_hung_up", "hangup_cause": 16, "duration_seconds": 20,
 "duration_source": "pbx", "timeline": {"…": "…", "ended_at": "…06:56:34.241Z"},
 "wrapup_pending": true, "reservation": {"active": true},
 "actions": {"can_wrapup": true, "wrapup_outcomes": ["connected", "no_answer", "busy", "promise_discussed",
             "dispute", "wrong_number", "do_not_call"]}, "poll_after_ms": null}
```

Without bridge evidence `wrapup_outcomes` is `["no_answer", "busy", "do_not_call"]` (the P0 evidence rule). While uncertain: `"uncertain": {"reason": "ami_connection_lost", "since": "…", "note": "…the reservation stays"}`, `poll_after_ms: 5000`. Event kinds: `agent_channel_created`, `agent_ringing`, `agent_answered`, `agent_dial_started/ended`, `originate_response`, `agi_allow`/`agi_deny`, `dialplan_<state>` (agent_answered, authorized, customer_dialing, customer_created, customer_answered, dial_end, customer_ended, agent_ended, denied), `customer_channel_created`, `customer_ringing`, `customer_answered`, `customer_dial_started/ended`, `bridge_enter/leave`, `<leg>_hangup`. Numbers are never in the events; the dialled number follows `clints_details` as in P0. Decision: **polling with ETag**, not long-polling: the API runs sync gunicorn workers, and a held request would pin one per open panel.

**Cancel** `POST /v2/calls/<id>/cancel {"expected_revision": 1, "reason": "wrong contact"}` (own call with `calls_make`, or `calls_manage`) -> `200` live view, `state: cancelled`, reservation `cancel_unsent`. Only while the queue row is still `queued`; after the claim `409 already_dispatching`. Stale revision `409 revision_conflict`.

**My active attempt** `GET /v2/me/calls/active` -> `{"attempt": <live view>}` for the attempt that still holds my reservation (live, uncertain or awaiting wrap-up), else `{"attempt": null}`.

**Uncertain attempts** `GET /v2/calling/uncertain?page=&page_size=` (`calls_manage`, case scope) -> `{items: [call detail + reconciliations (3) + open flags], total, page, page_size}`.

**Resolve** `POST /v2/calls/<id>/resolve` (`calls_manage`):

```json
{"expected_revision": 7, "resolution": "ended", "reason": "listened to the recording: they talked", "release_reservation": true}
```

-> `200` live view; `call_reconciliations` gets a `supervisor` row with the reason; an open `reconciliation_unresolved` flag is closed with the same reason; `release_reservation` releases with source `reconciled`. Refusals: no reason `422`; not uncertain `409 not_uncertain`; the service saw live channels in the last 2 minutes `409 call_still_live`; collectors `403`. A supervisor resolution never invents bridge evidence: without `bridged_at` the conversation outcomes stay unavailable.

**Review flags** `GET /v2/calling/review?state=open|resolved|all&kind=wrapup_needs_review,late_evidence,evidence_conflict,reconciliation_unresolved` (`calls_manage`) -> `{items: [flag + call], total, counts_by_kind, state, page, page_size}`. Close one with `POST /v2/calling/review-flags/<id>/resolve {"reason": "do-not-call applied on the case"}` -> `{flag_id, call}`; `409 flag_already_resolved` the second time.

**Wrap-up** is P0's `POST /v2/calls/<id>/wrapup`, unchanged for normal cases; section 8 for locked/archived ones.

## 6. The evidence model

- **Raw evidence** stays P0's `call_events` (append-only, deduplicated per epoch) and `call_legs`. After every stored event the consumer re-projects the attempt from its **whole** stored history of its **own** PBX epoch, ordered by the PBX `Timestamp` (never arrival order), plus its FastAGI decisions. A projection error never loses the event (savepoint); the reconciler re-projects in-flight attempts every pass.
- **Agent leg** = the app-issued `cawp-<attempt>` channel. **Customer leg** = the destination of a `DialBegin` from the agent leg. Anything else linked to the attempt is `other`.
- **Steps**: agent ringing/answered = agent `Newstate` 4-5 / 6 (or `OriginateResponse Success`); customer dialing = the `DialBegin`; customer answered = the customer leg's `Newstate 6` or `DialEnd ANSWER`.
- **Bridged (a conversation)** needs all of: the gate passed (an AGI `allow` or the trusted `UserEvent CollectionsCall State: authorized`), a customer leg dialled by the agent leg, the customer answered, and both legs in the **same bridge at the same time** (both `BridgeEnter` before either `BridgeLeave`/`Hangup`). Two answered legs, or a bridge left before the other leg joined, are not a conversation (tested).
- **End**: when the agent leg and every customer leg hung up. With a customer leg -> `ended` with the reason from the evidence (`customer_hung_up`, `agent_hung_up`, `customer_busy`, `customer_no_answer`, `customer_congestion`, `customer_unavailable`, `agent_hung_up_while_ringing`, `not_bridged`). Without one and without an AGI allow -> `failed` (`agent_no_answer`, `agent_busy`, `agent_rejected`, `agent_unavailable`, `agent_hung_up`, `authorization_denied`) and the reservation is released (`no_customer_contact`). If the gate allowed a dial but no customer leg was seen, the projection **waits** (more evidence may arrive; if none does, the stale check makes it uncertain).
- **Who hung up** (`customer_hung_up` / `agent_hung_up`): the leg the PBX reports a `HangupRequest` for (the other leg only gets a `SoftHangupRequest` from the bridge), then the first to leave the bridge, then the first hangup. Finding: the two hangup handlers' `UserEvent`s run within microseconds in either order, so comparing hangup times mislabelled about 1 call in 6 (caught by the full suite, reproduced with 12 live calls, fixed, 0 of 12 afterwards).
- **Hangup cause**: the customer leg's Q.850 cause (else the agent's). **Duration**: the bridged time (from the later of the bridge and the answer, to the first leave or hangup), `duration_source: pbx`; **NULL** when not bridged or when the end was never observed.
- **Ambiguous topology** -> `uncertain`: a second customer leg (`multiple_customer_legs`) or a bridge with more than the two legs (`bridge_topology_ambiguous`).
- **After a wrap-up** nothing on the attempt changes: new evidence is attached and a `late_evidence` flag (one open per attempt) points at it. A failed attempt that later shows customer evidence gets an `evidence_conflict` flag; a terminal `ended` attempt only gets facts filled in (out-of-order evidence), never its state changed.
- Out-of-order arrival gives the same final state (tested with shuffled and reversed recordings); a duplicate `UserEvent` is stored once (the same state again later changes nothing); a forged `UserEvent` (not on the app's channel) is not attributed; events of another PBX epoch are kept but never projected.

## 7. Uncertainty and reconciliation

Uncertain (reservation always kept): `ami_connection_lost`, `calling_service_stopped`, `calling_service_restarted`, `pbx_epoch_changed`, `evidence_lost` (P0), plus `originate_unacknowledged`, `dispatch_outcome_unknown` (a `sending` row older than 60 s: the process died mid-send), `evidence_missing` (no evidence `ring + 30` s after dispatch), `evidence_stale` (none for `limit + ring + 60` s), `multiple_customer_legs`, `bridge_topology_ambiguous`. An uncertain attempt cannot be wrapped up.

The reconciler (calling service, own thread, every `CALLING_RECONCILE_SECONDS`, 50 attempts per pass with a rotating cursor so stuck ones never starve new ones):

1. Same epoch: one `CoreShowChannels` inventory per pass; any related channel (its agent channel id, linked channels, known legs) -> kept (`channels_present`). Evidence younger than 5 s -> wait. Another epoch: that process and its channels are gone.
2. Then the evidence and the CDR decide:

| Evidence / CDR | Decision |
|---|---|
| both legs were bridged | `ended` (`bridged_then_lost`, or `evidence_complete` when the end was observed) |
| no AGI allow, no customer leg (same epoch: only once the token expired) | `failed` (`no_customer_contact`), reservation released |
| ambiguous topology | kept (`ambiguous_topology`) + `reconciliation_unresolved` flag |
| CDR: the customer answered, no bridge evidence | kept (`conversation_unproven`) + flag |
| CDR: dialled, not answered | `ended` (`cdr_not_answered`) |
| complete terminal evidence with a non-answer dial status | `ended` (`evidence_complete`) |
| otherwise | kept (`evidence_incomplete`); a flag after 5 minutes |

Every decision is appended to `call_reconciliations` (a kept one only when it changes), with the inventory summary, the CDR summary and the evidence summary. The reason code of the uncertainty is kept on the attempt; the resolution is in the log. Unknown facts stay NULL (e.g. a call killed while bridged ends with `duration_seconds: null`, `hangup_cause: null`).

**CDR lookup** (`cdr_lookup.py`, lead's review): the `userfield` tag `cawp:<attempt>` first; a full `uniqueid`/`linkedid` tag where the store keeps it (the test CSV); a **truncated** `uniqueid` (Issabel's `varchar(32)`) only when P4's `resolve_attempt_tags` says it names exactly one attempt and it is this one; always inside a window from 1 h before the attempt to 12 h after (Riyadh wall clock, as the PBX writes `calldate`). Sources: `none` (default), `csv` (the test PBX, mounted read-only), `mysql` (the `CDR_*` settings; refused for 10.10.0.220 until commissioned). A CDR ANSWERED is never treated as a conversation.

## 8. The lead's decision: wrap-up on a locked or archived case

Implemented for PBX wrap-ups (`POST /v2/calls/<id>/wrapup`):
- the wrap-up **is recorded**: outcome, reviewer, and the note text in `call_wrapups.note_text`; `wrapped_up_at` is set and the reservation released (`wrapup`);
- **not applied**: the legacy `"Comments"` row (`note_id` NULL), the callback follow-up, the promise, the contact block (wrong number) and the case block (do-not-call). What the collector asked for is kept in `deferred_actions` (note status/method/reachability, `callback_at`, `promise`, `wrong_number`, `do_not_call`);
- the wrap-up is `needs_review` with `review_reason` `case_locked` | `case_archived`, and a `wrapup_needs_review` flag lists it in `GET /v2/calling/review`;
- the P0 evidence rule still applies (`connected` without bridge evidence -> 409);
- **safety addition:** when the deferred outcome is `do_not_call` or `wrong_number`, the flag has `blocks_calling = true`: dispatch (API, final recheck and the gate) refuses the case with `calling_review_pending` until a supervisor resolves the flag, so an opt-out is honoured even though its block could not be written on the locked case. The supervisor applies the change through the normal screens after unlocking, then resolves the flag with a reason;
- 0053 guards it: a flagged wrap-up must have the note text and no applied protected change; an unflagged one must have its legacy note; the do-not-call CHECK is relaxed only for flagged wrap-ups.

A **manual** log on a locked/archived case is still refused (409, P0 test unchanged): it is a new record at the collector's initiative, with no live reservation to release. If the product wants manual logs handled the same way, it is a small change in `log_manual_call`.

## 9. Test PBX changes (test only)

- Agent **7204** rings 3 s, then answers (to observe `agent_ringing`, and to read the token while it rings in the replay test).
- The entrypoint now **supervises Asterisk** like `safe_asterisk`: a crashed process is restarted after writing a **new epoch** (the pre-start hook contract). `docker stop` still stops it.
- A **control port 5099** (compose network only, `PBX_TEST_CONTROL_KEY`, busybox `nc`): `restart` writes a new epoch and runs `core restart now`; `crash` sends SIGKILL. Finding: a graceful `core restart` hangs up every channel and reports it before AMI closes, so a live call **ends by evidence**; only a crash (or a network loss) produces the uncertain path. Both are tested. Nothing like this port goes to Issabel (runbook §15).

## 10. Tests

```
docker compose --profile pbx-test up -d pbx-test
CALLING_ENABLED=1 CALLING_RECONCILE_SECONDS=3 \
CALLING_DATABASE_URL=postgresql+psycopg2://postgres:postgres@test-db:5432/cawp_test_call1 \
    docker compose --profile pbx-test up -d calling
docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call1 api-test tests/test_calling_c2c_*.py
```

| File | Tests | What |
|---|---|---|
| `test_calling_c2c_rules.py` | 15 | every dispatch rule through `POST /v2/calls` (writes nothing when refused), the happy path (attempt, reservation, queue row, audit, idempotent replay), queue presence never authorizes, the real `transport_status()`, the service-side recheck of role and account |
| `test_calling_c2c_dispatch.py` | 14 | the dispatcher with a fake AMI session: the one-shot token (only its hash stored, bound to epoch/extension/destination, never in the audit), the exact extension-first Originate, Success/Error/lost-ack outcomes, the final recheck (locked, DNC, blocked contact, extension moved, window, number changed), another epoch never sent then expired, disconnected then expired, cancel before/after the claim, two dispatchers send exactly once, queue guards |
| `test_calling_c2c_projection.py` | 15 | **recorded** AMI sequences through the real consumer: answered (bridged then ended, cause 16, duration), busy, agent no answer (released), shuffled and reversed arrival give the same result, missing bridge (wrap-up `connected` refused), bridge left before the other leg joined, duplicate UserEvent, forged UserEvent, another epoch, second customer leg and third bridge member (uncertain), truncated evidence stays live, late evidence after wrap-up flagged, customer evidence after a failure flagged; pure `derive()` |
| `test_calling_c2c_reconcile.py` | 13 | disconnect and epoch change -> uncertain with reservations kept; live channels keep it uncertain and block a supervisor resolution; no customer contact (waits for token expiry) -> failed + released; CDR answered without bridge -> human (resolve with reason + release); CDR not answered -> ended; no CDR -> kept then flagged; complete evidence after reconnect; missing evidence; a send that never finished; a reconciler pass; the CSV CDR lookup (userfield, truncated uniqueid only when unique, date window); pure `decide()` |
| `test_calling_c2c_api.py` | 11 | live view shape, ETag/304, my active attempt, `/v2/me/calling` readiness, uncertainty and actions, the uncertain list (scope, manager only, resolve), **the locked/archived decision** (locked: facts recorded, nothing protected applied, reservation released, flag; archived do-not-call: not applied, blocks calling until reviewed, review list, resolve flag; wrong number deferred; evidence rule still applies; DB guards), `wrapup_pending` for no-contact failures, the `no_customer_contact` guard |
| `test_calling_c2c_agi.py` | 6 | the gate refuses when the case was locked/archived, marked do-not-call, put under a blocking review or the contact was blocked while the agent rang (token not consumed); allows otherwise |
| `test_calling_c2c_live.py` | 9 | **live, from the attempt API to the final state**: answered call (bridged, then ended, `customer_hung_up`, 20 s, cause 16, ETag 304, wrap-up `connected` releases); busy customer (`customer_busy`, 17, `connected` refused, `busy` saved); customer never answers (`customer_no_answer` after the 20 s ring); customer hangs up (3 s); agent never answers (`agent_no_answer`, released, no AGI decision) and agent busy (`agent_busy`); **replayed token**: the token read over AMI while 7204 rang, the Originate replayed after the wrap-up -> AGI `token_consumed`, no second customer Dial, the attempt and wrap-up unchanged, `late_evidence` flagged; **PBX crash mid-call** -> uncertain (reservation kept, wrap-up refused) -> new epoch -> reconciled `ended` (`bridged_then_lost`) -> wrap-up; the next call works on the new epoch; crash while the agent rings -> reconciled `failed` `no_customer_contact`; graceful restart mid-call ends by evidence |

Results:
- `tests/test_calling_c2c_*.py`: **83 passed** (live file about 90 s).
- All calling tests (P0 + P1) pass; the P0 live tests pass with the new dispatcher, projection and gate running.
- **Whole suite: 1040 passed, 1 skipped, 0 failed** (5 min 21 s, final run after restarting the calling service on the final code) with the test PBX and the calling service up, including `test_api_response.py` (the lead's isolation report: my test modules import `services.calling.dispatch`/`rules`, which load the app config, only inside tests). The skip is the P0 host-only dialplan file check; run on the host it is equal.
- The recorded fixtures are re-recorded with `RECORD_AMI_FIXTURES=1` on a live run.

## 11. Decisions (for PROJECT-NOTES)

1. **Future callback rule**: an open follow-up due more than 15 minutes from now forbids click-to-call; from 15 minutes before it, and when late, calling is allowed.
2. **Closed/paid** = outstanding <= 0 (original minus approved, non-reversed payments).
3. **Handoff**: `call_dispatch_queue`, claimed `FOR UPDATE SKIP LOCKED` by the calling service, only for its own PBX epoch; the token is issued at the claim by the sending process. Nothing is ever retried or re-sent.
4. **`no_customer_contact` reservation release**: evidence-proven (no AGI allow, no customer leg), database-guarded; used by live failures, never-sent dispatches and reconciliation. Restarts and timers still never release.
5. **Locked/archived wrap-up**: as the lead decided, plus `blocks_calling` for a deferred do-not-call or wrong number; manual logs stay refused.
6. **Live panel**: polling with ETag (`poll_after_ms` 1000 live / 5000 uncertain / null final), not long-polling.
7. **A graceful PBX restart is evidence, a crash is not**: the first ends the call by the reported hangups, the second makes it uncertain until reconciled.
8. **Operational note**: the `calling` container mounts the code but Python loads it once; restart the service after deploying calling code (an intermittent live failure during this stream was a stale service process).

## 12. Open items

1. **Integration for the lead**: add 0052–0053 to the `db/README.md` list; `CAWP-API/DEPLOY.md` §5d should list the P1 variables (runbook §12 item 5 has them); register `CALLING_CDR_SOURCE=mysql` on the calling host at commissioning.
2. **Server-side hang-up / stop** is still impossible (P0 decision 7: the AMI account has no `call` write). A supervisor can resolve an uncertain call but cannot end a live one; the PBX limit (`TIMEOUT(absolute)`, 600 s) bounds it.
3. **Transfers** (`SwapUniqueid`, `BridgeMerge`, attended transfer) are not modelled: endpoint transfers are disabled by the dialplan check; any extra bridge member makes the attempt uncertain.
4. **Clock skew**: step timestamps use the PBX clock, dispatch/AGI times the database clock; both must be NTP-synced (commissioning check).
5. **The API's calling settings are environment variables** (context, ring, limit, TTL) and must match the installed dialplan; a mismatch denies at the gate, never dials. A reviewed settings table could replace them.
6. **Retention** for `call_events`, `call_reconciliations` and `calling_service_status` rows of stopped workers is not planned yet.
7. **Manual log on a locked/archived case** stays refused (section 8); confirm with product.
8. **Frontend**: the live-call panel, the uncertain list and the review list are API-only here.

## 13. Containers

`cawp-pbx-test` (rebuilt image `cawp-pbx-test:20.11.1` with 7204, the supervisor loop and the control port) and `cawp-calling` (`CALLING_ENABLED=1`, test database `cawp_test_call1`) were used for the runs and stopped afterwards; start them with the commands in section 10. P4's CDR volume data was only read. No other container was touched.
