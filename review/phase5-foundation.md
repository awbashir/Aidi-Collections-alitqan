# Phase 5 P0: human-calling foundation

Stream P0 of `docs/phase5-spec.md`, on branch `phase-5-calling` (not committed). The real Issabel PBX (10.10.0.220) and every external SIP or phone network were **never contacted**. Everything was proven against a local Asterisk test PBX in Docker.

## 1. Summary

| Piece | Where | State |
|---|---|---|
| Local test PBX (Asterisk 20.11.1 LTS, PJSIP WebRTC/UDP, AMI + TLS, loopback customer network, MixMonitor, CDR CSV, AGI hook, epoch, health) | `pbx-test/`, compose service `pbx-test` (profile `pbx-test`) | exercised by 10 live tests (stopped after the final run) |
| Issabel production runbook, with PBX-side and calling-host verification scripts | `docs/issabel-commissioning.md`, `CAWP-API/calling/issabel/*`, `python -m calling.verify` | both verifiers pass against the test PBX |
| AMI client: codec, persistent session, supervisor with PBX epoch | `CAWP-API/services/calling/ami.py` | 40 fake-server/codec tests + live |
| FastAGI authorization hook (one-shot tokens) | `CAWP-API/services/calling/agi.py` | 20 tests + live end to end |
| Data model (migrations 0048–0051) | `db/migrations/0048_*`–`0051_*` | 10 guard tests; applied cleanly by the test harness |
| Manual call log, reviewed wrap-up, lists, settings, admin | `CAWP-API/api/calling.py`, `services/calling/*` | 30 API tests |
| `calling` service (AMI owner + FastAGI), off by default | `CAWP-API/calling/`, compose service `calling` | ran against the test PBX and the test DB |
| Tests | `CAWP-API/tests/test_calling_*.py`, `tests/fixtures/seed_calling.sql` | **156 passed, 1 skipped** (a host-only file check, done by hand: equal). Whole suite: **929 passed, 1 skipped** with the test PBX and `calling` up; 919 passed, 11 skipped with them down |

## 2. Files

New:
- `pbx-test/`: `Dockerfile`, `entrypoint.sh`, `policy.json`, `conf/*` (13 Asterisk files, two of them templates with `@@…@@` placeholders).
- `docs/issabel-commissioning.md`.
- `db/migrations/0048_calling_extensions_settings.sql`, `0049_call_attempts_reservations.sql`, `0050_call_evidence.sql`, `0051_call_wrapups_blocks.sql`.
- `CAWP-API/api/calling.py`: registered by the existing `api.calling` entry in `api/__init__.py`; 20 routes, no conflicts with the full `app.py`.
- `CAWP-API/services/calling/`: `__init__`, `windows`, `dialplan`, `ami`, `agi`, `originate`, `common`, `extensions`, `settings`, `attempts`, `evidence`, `wrapup`, `queries`.
- `CAWP-API/calling/`: `__init__`, `__main__`, `main` (the service), `config`, `verify` (commissioning checks), `issabel/pbx-epoch.sh`, `issabel/pbx-verify.sh`, `issabel/collections-globals.conf.example`.
- `CAWP-API/tests/test_calling_{helpers,windows,config,ami_protocol,ami_session,migrations,api,agi,service,live_pbx}.py`, `CAWP-API/tests/fixtures/seed_calling.sql`.

Changed:
- `docker-compose.yml`: the `pbx-test` and `calling` services and three volumes (`cawp_pbx_test_recordings`, `cawp_pbx_test_cdr`, `cawp_pbx_test_keys`). Nothing else changed.
- `CAWP-API/DEPLOY.md`: new §5d.

Not touched: `app.py` (`/cdr*` unchanged), `models.py` (every new column and table is unmapped), `core/permissions.py` (the keys were already registered), `conftest.py`, `seed.sql`, the frontend.

## 3. Test PBX design (`pbx-test/`)

- **Image:** `alpine:3.22@sha256:5291449c…e5fa8` (pinned by digest) plus pinned packages `asterisk=20.11.1-r6`, `asterisk-srtp=20.11.1-r6` and `pjsua=2.15.1-r0`, with `openssl`, `tzdata` and `bash`. Local tag `cawp-pbx-test:20.11.1`, `mem_limit 256m` (it uses about 60 MB).
- **Secrets:** they come only from the environment. The compose file holds labelled **test-only** values: `pbx-test-ami-only`, `pbx-test-sip-only`, `pbx-test-agi-only`. The entrypoint refuses values outside `[A-Za-z0-9._-]{8,128}` (so a value cannot inject config lines) and renders the templates at start. Nothing secret is baked into the image.
- **Certificate:** self-signed (SAN `pbx-test`, `localhost`, `127.0.0.1`), generated on first start into the `cawp_pbx_test_keys` volume. It is used by HTTPS/WSS (8089) and AMI TLS (5039). The `calling` service mounts it read-only as its CA.
- **PJSIP:**
  - 7001–7005 are WebRTC agents: WSS transport, `webrtc=yes` (DTLS-SRTP, ICE, AVPF, rtcp-mux), `dtls_auto_generate_cert`, ulaw/alaw.
  - 7101–7102 are plain UDP agents. **7101 is registered by a bundled pjsua client** (a real SIP UA with digest auth, auto-answer, null audio).
  - 7201–7203 are simulated agent phones with static loopback contacts: 7201 answers, 7202 is busy, 7203 never answers.
  - Every agent and the trunk have `allow_transfer=no`.
- **Loopback "customer network":** trunk `cawp-test-trunk` points to `127.0.0.2:5160` inside the container; the identify rule sends everything from there to context `pbx-test-sim-network`. The **last digit** of the E.164 number decides the behaviour: `1` answers with a tone then echo, `2` busy (486), `3` rings forever, `4` answers then hangs up after `PBX_TEST_SHORT_CALL_SECONDS` (3), `5` congestion, `6` answers after 3 s with a tone. It never leaves the container.
- **AMI:** plain 5038 and TLS 5039, private-range ACL, `timestampevents=yes`. Account `cawp-calling` has `read=call,user,system` and `write=originate,reporting`. Verified live: `Command`, `Setvar`, `Reload`, `Redirect` and application `Originate` are refused.
- **Dialplan:** `extensions_collections.conf` is the **generator's output** for `policy.json` (24/7 windows, 600 s maximum, 20 s ring, recording on). A test checks that the committed file equals the generator's output (the file is outside the test container, so the check runs on the host; it passes). Agent endpoints use `cawp-agents`, which can dial only `_7XXX` and echo `600`, and never the restricted context.
- **AGI hook and globals:** the entrypoint writes `/run/asterisk/collections-globals.conf` (`COLLECTIONS_AGI_URL=agi://calling:4573/collections-authorize`, `COLLECTIONS_AGI_KEY`) and a **new epoch UUID on every container start** (`collections-pbx-epoch.conf`), both included in `[globals]`.
- **Recording:** MixMonitor (bridged audio only) writes `monitor/YYYY/MM/DD/cawp-<attempt>.wav` into the volume `cawp_pbx_test_recordings`. The files exist; the simulated customers send no RTP, so they hold only the WAV header.
- **CDR:** `cdr_csv` writes `Master.csv` into the volume `cawp_pbx_test_cdr`. I chose CSV over the `cdr-test-db` MySQL because ODBC/MySQL drivers are not in the Alpine package set and CSV is reliable. Each row carries `uniqueid=cawp-<attempt>` and `userfield=cawp:<attempt>`. Issabel writes the same fields to MySQL.
- **Health:** the compose healthcheck is `asterisk -rx 'core waitfullybooted'`; `init: true` reaps pjsua. Only `127.0.0.1:8089` is published. AMI, SIP and RTP stay on the compose network.
- **Run it:** `docker compose --profile pbx-test up -d pbx-test`.

## 4. What differs on Issabel

The runbook's §15 has the full table. In short:
- Issabel runs whatever Asterisk and channel driver runbook §1 finds (often 13 or 16 on Issabel 4), not 20. `webrtc=yes` needs Asterisk 16 or later; the runbook gives the explicit options for older versions and a chan_sip variant.
- The configuration goes into the FreePBX `*_custom.conf` files.
- The real trunk replaces the loopback, and only authorized test numbers may be dialled.
- Certificates come from a trusted CA (company or public), not a self-signed one.
- The AMI ACL is the calling host's IP only.
- Secrets come from the vault. The calling service refuses the test-only values for any other PBX.
- The epoch is written by `pbx-epoch.sh` as a pre-start hook. Crash restarts by safe_asterisk are covered because the epoch also includes the process start time.
- The calling windows are the approved hours, not 24/7.
- CDR goes to MySQL, and `codec_opus` is optional.
- The generated dialplan dials the trunk **directly**. FreePBX outbound-route caller-ID and prefix rules therefore do not apply (open item 2).

## 5. AMI client (`services/calling/ami.py`)

This ports the rebuild's `ami-protocol.js` and `ami-session.js` designs to Python threads. No Node code was used.

- **Codec:**
  - It needs the greeting, then CRLF frames.
  - Repeated keys keep their order.
  - Routing keys (`ActionID`, `Event`, `Response`, `Action`) must not repeat.
  - Limits are 64 KiB per frame, 8 KiB per line and 256 fields. A violation "poisons" the decoder: it rejects everything afterwards and the session closes.
  - Outbound actions need exactly one `Action` and one `ActionID`, no `Event`/`Response`, and no control characters, so header injection is impossible.
  - Deviation from the rebuild: a value that is not UTF-8 is decoded with replacement characters instead of poisoning the stream (PBX caller names can be Latin-1). Routing values are ASCII anyway.
- **Session (`AmiSession`):**
  - TLS 1.2+ with certificate and host-name verification. Plaintext is allowed only to loopback or an explicitly listed test host.
  - It waits for the greeting, logs in with `Events: on`, and correlates every action by its `<session>:<n>` ActionID. Unknown responses are counted and ignored. `OriginateResponse` goes to the event consumer.
  - Actions are limited to an allowlist (`Ping`, `Originate`, `Hangup`, `CoreStatus`, `CoreShowChannels`, `Getvar`, `Status`, `BridgeInfo`).
  - Events are delivered in order by one consumer thread from a bounded queue. The session closes on overflow, on a consumer exception, or when the consumer is stalled longer than `event_timeout`.
  - A `Ping` heartbeat runs; a missing or wrong Pong closes the session with `ami_heartbeat_failed`.
  - An action timeout closes the session (a lost acknowledgement leaves the PBX state unknown). Nothing is replayed.
  - `list_channels()` collects `CoreShowChannels` rows under their ActionID and needs the exact `ListItems` count.
- **PBX identity (epoch):** `read_identity` combines the global `COLLECTIONS_HUMAN_PBX_EPOCH` (Getvar, must be a UUIDv4) with CoreStatus `CoreStartupDate`/`Time`, as `epoch = "<uuid>@<startup>"`. A missing UUID is recorded as missing (strength `startup_only`), never invented. Reloads and reconnects keep the epoch; a restart changes it. The login greeting event (`FullyBooted`/`Uptime`) is only informational: it has no `Timestamp` and is not unique.
- **Supervisor (`AmiSupervisor`):**
  - It reconnects with exponential backoff (1–30 s).
  - After each login it reads the identity **before** releasing events, so every event belongs to exactly one epoch.
  - Callbacks: `on_session_start(ctx)` (with the previous identity and `pbx_restarted`), `on_event`, `on_heartbeat`, `on_session_end(ctx, code)`.
  - Live on the test PBX: a restart was detected within about 1 s, with "PBX RESTARTED" and the new epoch.

## 6. FastAGI hook (`services/calling/agi.py`)

The generated dialplan runs `AGI(${GLOBAL(COLLECTIONS_AGI_URL)})` on the **agent** channel after the agent answered. The server:

1. checks the peer allowlist (CIDRs or host names), a bounded number of connections and a 5 s deadline for the whole exchange;
2. requires `agi_network=yes`, the script name `collections-authorize`, a `PJSIP` or `SIP` channel, a `collections-human-*` context, an E.164 extension and an `agi_uniqueid` of the form `cawp-<uuid>`;
3. **clears** all five gate variables;
4. compares `${GLOBAL(COLLECTIONS_AGI_KEY)}` with `CALLING_AGI_SECRET` in constant time;
5. reads `COLLECTIONS_ATTEMPT`, `COLLECTIONS_TOKEN` and `${GLOBAL(COLLECTIONS_HUMAN_PBX_EPOCH)}`;
6. in the database, locks the token row (`FOR UPDATE`) and checks, in order: not consumed, not expired, same attempt and channel id, same context and destination, the channel name is the token's extension, the same epoch (when the token is bound to one), and the attempt is not in a terminal, uncertain or prepared state. It then consumes the token, records an `allow` decision and **commits**;
7. checks that the epoch did not change meanwhile, sets destination, extension, limit and expiry, and sets `ALLOW` **last**.

Every deny is recorded with a fixed reason: `token_consumed`, `token_expired`, `token_mismatch`, `extension_mismatch`, `epoch_mismatch`, `agi_key_mismatch`, `peer_not_allowed`, `agi_timeout`, `channel_hangup`, and so on. A lost reply after the commit leaves the token consumed and is recorded as `allow_not_published`; a token is never granted twice (there is a unique index on one `allow` per token). In P0 the decision is the token table only; P1 adds the dispatch rules before issuing tokens.

## 7. The `calling` service (`python -m calling`)

- **Off by default:** with `CALLING_ENABLED=0` it opens no socket, needs no secret and is healthy.
- **When enabled:**
  - it refuses any host other than `pbx-test`/`cawp-pbx-test` until `CALLING_PBX_COMMISSIONED=1`;
  - it refuses any name that resolves to 10.10.0.220 without that flag;
  - it refuses the test-only secrets for any other PBX, and plaintext AMI except to loopback or a test host.
- **Startup:** the recovery step marks attempts that were in flight **on a PBX** (`pbx_epoch` set) as `uncertain`, with reason `calling_service_restarted`. It never releases a reservation.
- **Running:** one supervised AMI session (TLS to `pbx-test:5039` in compose) feeds the evidence consumer. The FastAGI server listens on 4573. An internal status is served on port 8791 (`/status`, `/healthz`, not published). The loop writes the health file every 5 s. `--healthcheck` fails when the file is stale, when the AMI session has been down longer than `CALLING_HEALTH_AMI_GRACE`, when the AGI listener stopped, or when the database does not answer.
- **Evidence consumer (`services/calling/evidence.py`):**
  - It keeps only correlated events: a `UserEvent CollectionsCall` whose own Linkedid is `cawp-<AttemptId>` (a forged one is kept as `untrusted` and not attributed); channel, dial, bridge and hangup events and `OriginateResponse` whose Uniqueid or Linkedid (or DestUniqueid/DestLinkedid) is a `cawp-<uuid>` channel; and the lifecycle events `FullyBooted`, `Shutdown` and `Reload`.
  - Fields are allowlisted, with no caller-ID names or numbers. `pbx_ts` is the PBX `Timestamp`. The dedupe key includes the epoch.
  - It projects `call_legs`: the agent leg is the app-issued channel, and the customer leg is the destination of a DialBegin from the agent. It records ringing, answer, bridge and hangup cause as set-once facts. It does **not** advance the attempt state; that is P1.
  - On a database outage it buffers up to 5,000 events in order, and also keeps an unrecorded session start. When the buffer overflows it records an `EvidenceGap` and marks the affected in-flight attempts `uncertain` (`evidence_lost`).
  - When a session ends it records the disconnect code and a gap, and marks in-flight attempts of that epoch `uncertain` (`ami_connection_lost`, or `calling_service_stopped` on shutdown). A new epoch marks older-epoch attempts `uncertain` (`pbx_epoch_changed`). Reservations are never touched.
- **Shutdown:** `SIGTERM` stops accepting AGI connections, waits at most 6 s for answers in flight, closes AMI, writes the final health file and exits 0. Measured: under 1 s.

## 8. Data model and lifecycle (0048–0051)

All tables are new, with no FK to `clints`, `users` or `"Comments"` (the legacy code hard-deletes those). Guards raise SQLSTATE `CL001` (append-only / immutable), `CL002` (set-once or identity), `CL003` (transition) or `CL004` (reservation release rule).

| Table | Purpose and rules |
|---|---|
| `user_extensions` | Ownership history. Partial unique indexes allow **one active owner per extension** and **one active extension per user**. A row can only be released, once, with a reason; it is never deleted. |
| `calling_preferences` | One row per user: `auto_answer_attempts` (default true; only attempt-tagged calls), `devices` {microphone, speaker, ringer}, `revision`. No secrets. |
| `calling_window_policy` (+ `_history`, append-only) | One reviewed JSON policy: Riyadh `windows` (days 0=Sunday..6, `HH:MM`, start inclusive, end exclusive, overnight belongs to the start day, union across the week) plus optional `holidays`. Revision-checked. No row = `calling_windows_missing`. |
| `call_attempts` | One row per call, manual or PBX. The lifecycle is below. Identity columns are immutable; step timestamps, `pbx_epoch` and `pbx_channel_id` (`cawp-<id>`) are set once. `uncertain` needs a `reason_code`. `duration_seconds` NULL means unknown, and it needs a `duration_source` (`reported`/`pbx`). A manual attempt is always `ended` with no PBX fields. |
| `call_reservations` | Partial unique indexes allow one **active** reservation per collector, per case and per extension. The release sources are only `wrapup` (needs the saved wrap-up), `cancel_unsent` (a cancelled, never-dispatched attempt) and `reconciled` (needs a person and a reason). **There is no timeout or restart source, so the database itself makes "never auto-released" hold.** |
| `call_dispatch_tokens` | The one-shot AGI authorization: SHA-256 only, one per attempt, bound to channel id, context, E.164 destination, extension, limit and (optionally) epoch UUID; expires within 10 minutes; consumed once. |
| `pbx_sessions` | One row per AMI session: epoch, strength, previous epoch, `pbx_restarted`, connect time, and the disconnect (set once). |
| `call_events` | Raw sanitized evidence, **append-only** (UPDATE, DELETE and TRUNCATE are refused), deduplicated. |
| `call_legs` | Projected per-channel facts (epoch + Uniqueid), set once; a role can only move from `unknown`. |
| `call_agi_decisions` | Every allow and deny with its reason, append-only. |
| `call_wrapups` | **One per attempt**, immutable. Outcome enum `connected, no_answer, busy, promise_discussed, dispute, wrong_number, do_not_call`. It links `note_id`, `follow_up_id`, `callback_at`, `promise_id` (CHECK: only with `connected`), `contact_blocked` (only with `wrong_number`), `calling_block_id` (required with `do_not_call`) and `reviewed_by`. |
| `calling_blocks` | Case-level `do_not_call`: one active per case, released only explicitly with who and why. |

Lifecycle (`calling_attempt_allowed_next`):

```
prepared -> authorized -> dispatched -> agent_ringing -> agent_answered -> customer_dialing
         -> customer_answered -> bridged -> ended
prepared | authorized            -> cancelled | failed | uncertain
dispatched .. customer_dialing   -> ended | failed | uncertain
customer_answered | bridged      -> ended | uncertain
uncertain                        -> ended | failed        (reconciliation only, P1)
ended, failed, cancelled: terminal. Steps may be skipped forward, never backwards.
```

**Decisions (please record them in PROJECT-NOTES):**

1. **`user_extensions` is the source of truth; `users.extension` is a mirror.** Assign and release write the legacy column (CDR attribution in RPT-23/25 and the legacy user form still read it). Legacy values are **never imported silently**: cawp-db has 97 users with a value, 73 distinct, including duplicates and `0`/`-1`. `GET /v2/calling/extensions/legacy-drift` classifies every difference (`adoptable`, `legacy_invalid`, `legacy_owned_by_other`, `differs_from_owned`, `mirror_missing`, `user_inactive`), and `POST …/legacy-drift/adopt` turns one value into ownership with a reason. Other users who hold the same legacy value are reported (`legacy_conflicts`), not changed.
2. **Wrong number reuses the Phase 1 `"ClintContacts".is_blocked`**, with the Phase 1 rules: all rows of the logical contact; blocking the default needs `replacement_default_id` when other eligible contacts exist; the 0014 history trigger writes `contact.block`. **Do-not-call is a case-level `calling_blocks` row**; the Phase 1 model has no case flag.
3. **A manual call is an attempt** (`mode='manual'`, `state='ended'`) with its wrap-up in the same transaction, so "my calls", "case calls" and the call log are one list.
4. **For PBX attempts, outcomes that claim a conversation need bridge evidence.** `connected`, `promise_discussed`, `dispute` and `wrong_number` need `bridged_at`, otherwise 409 `conversation_not_evidenced`. `no_answer`, `busy` and `do_not_call` are always allowed. An `uncertain` attempt cannot be wrapped up until P1 reconciles it.
5. **A promise in a wrap-up needs `promise.agreed === true`, outcome `connected`, and the `promises` (or `clints_PayemntDates`) key.** It goes through `money_promises.create_promise`, so its rules apply (Riyadh due date, exact balance, lock).
6. **A wrap-up on a locked or archived case is refused** (the legacy note rule). A reservation on such a case waits until the case is unlocked (open item 5).
7. **The AMI account has no `call` write**, so no `Hangup` from the server (that would also allow `Setvar` on the AGI URL global). The maximum duration is enforced by the PBX (`TIMEOUT(absolute)` and `L`).

## 9. Endpoints (examples taken from the running code)

The conventions are those of `core/api_response.py`. Mutations need `Idempotency-Key`, and those that edit a row need `expected_revision` or `If-Match`.

**Log a manual call** (`POST /v2/calls/manual`, key `calls`):

```json
{"case_id": "b9bb…f3", "contact_id": "4e1…", "started_at": "2026-09-24T08:10:00+03:00",
 "duration_seconds": 184, "direction": "outbound", "outcome": "connected",
 "note": {"text": "Debtor agreed to pay 500 on the 1st", "comment_status_id": "…", "contact_method_id": "…"},
 "callback_at": "2026-09-25",
 "promise": {"agreed": true, "amount": "500.00", "due_date": "2026-10-01"},
 "transcript": "optional text"}
```

→ `201`, the call detail (the same shape as below) with `wrapup.promise_id`, `wrapup.follow_up_id` and `wrapup.note_id` filled in. The note is a legacy `"Comments"` row with `call_id` = the attempt id, so the follow-up trigger ran.

Refusals:
- `promise_discussed` with a promise → `422 promise_discussed_is_not_a_promise` ("'promise discussed' records a discussion only and never creates a promise; use outcome 'connected' with promise.agreed = true").
- A promise without `agreed: true` → `422 promise_not_agreed`.
- `started_at` in the future → `422 validation_failed {"started_at": "in_future"}`.
- Case locked → `409 case_locked`. Out of scope → `404`.

**Wrap up a PBX call** (`POST /v2/calls/<id>/wrapup`):

```json
{"expected_revision": 5, "outcome": "no_answer",
 "note": {"text": "No answer", "comment_status_id": "…", "contact_method_id": "…"}}
```

→ `200`:

```json
{"message": "wrap-up saved", "status": true, "data": {
  "id": "d48a…b8", "mode": "click_to_call", "direction": "outbound", "state": "ended",
  "case": {"id": "b9bb…f3", "name": "CL case", "account_number": "ACC-…", "missing": false},
  "contact": null, "destination": null, "collector": {"id": "8161…4e", "name": "CL One"},
  "extension": null, "campaign_id": null, "created_at": "2026-09-24T05:55:46.474875+00:00", "started_at": null,
  "timeline": {"authorized_at": "…", "dispatched_at": "…", "agent_ringing_at": null, "agent_answered_at": "…",
               "customer_dialing_at": "…", "customer_answered_at": null, "bridged_at": null, "ended_at": "…"},
  "duration_seconds": null, "duration_source": null, "duration_known": false,
  "pbx": null, "reason_code": null, "hangup_cause": null, "has_transcript": false,
  "wrapup": {"id": "9610…01", "outcome": "no_answer", "note_id": "67a5…1f", "follow_up_id": null,
             "callback_at": null, "promise_id": null, "contact_blocked": false, "calling_block_id": null,
             "reviewed_by": {"id": "8161…4e", "name": "CL One"}, "reviewed_at": "…", "note_text": "No answer"},
  "wrapup_pending": false, "revision": 6, "transcript": null,
  "reservation": null,
  "evidence": {"note": "PBX facts only. Two answered legs are not a conversation; bridge membership of both is required.",
               "events": [], "legs": [], "agi_decisions": []}}}
```

Refusals:
- `connected` without bridge evidence → `409 conversation_not_evidenced`.
- A second wrap-up → `409 wrapup_exists`.
- A stale revision → `409 revision_conflict`.
- An uncertain or live call → `409 wrapup_not_allowed`.
- Another collector's call → `403` (unless `calls_manage`); outside the case scope → `404`.

**Lists:**
- `GET /v2/calls/mine`, `GET /v2/cases/<id>/calls` and `GET /v2/calls` take `page`, `page_size` (≤ 200), `sort=-time|time`, `state`, `mode`, `outcome` (comma lists), `collector_id`, `case_id`, `campaign_id`, `wrapup=pending|done`, and `from`/`to` (Riyadh civil dates on the call time).
- The response is `{items, total, page, page_size, sort, counts_by_state}`.
- Numbers (`contact.value`, `destination`) are shown only with `clints_details`.

**My calling** (`GET /v2/me/calling`):

```json
{"user_id": "8161…4e", "extension": null,
 "preferences": {"auto_answer_attempts": true, "devices": {"microphone": null, "speaker": null, "ringer": null},
                 "revision": 0, "saved": false, "updated_at": null},
 "window": {"riyadh_time": "2026-09-24T08:55:46+03:00", "riyadh_weekday": "thu", "configured": false,
            "open": false, "reason": "calling_windows_missing"},
 "can_call": false, "reason": "no_extension"}
```

`can_call` is always false in P0. The reason is the first blocker, in order: `no_extension`, `calling_windows_missing`, `calling_window_closed`/`calling_holiday`, `transport_not_connected`, `click_to_call_not_enabled`. To save preferences: `PUT /v2/me/calling/preferences {"expected_revision": 0, "auto_answer_attempts": false, "devices": {"microphone": "mic-1"}}`.

**Admin** (`calls_manage`):
- `POST /v2/calling/extensions/7001/assign {"user_id": "…", "reason": "new desk", "expected_owner_id": null, "replace": false}` → `200 {extension, items: [history], legacy_holders, active, legacy_conflicts}`.
  - Refusals: `owner_changed`, `extension_owned` (use `replace`), `user_has_extension`, `extension_in_use` (active reservation), `user_inactive`.
- `POST …/release {"reason", "expected_owner_id"}`.
- `GET /v2/calling/extensions?state=active|released|all&q=`, `GET …/<ext>/history`, `GET …/legacy-drift`, `POST …/legacy-drift/adopt {"user_id", "extension", "reason"}`.
- `PUT /v2/calling/windows {"expected_revision": 0, "reason": "approved hours", "windows": [{"days": [0,1,2,3,4], "start": "09:00", "end": "20:00"}], "holidays": []}` → policy revision 1, the Riyadh status and the history. `GET /v2/calling/windows` is readable with any calling key.
- `GET /v2/calling/status` → the latest `pbx_sessions` row (as the DB sees it), in-flight counts, active reservations, AGI allow/deny counts for 24 h, and the window status.
- `GET /v2/calling/users?q=` is the user picker.
- Calling blocks: `GET /v2/cases/<id>/calling-blocks` (`calls`) and `POST /v2/calling-blocks/<id>/release {"reason"}` (`calls_manage`).

## 10. How P1–P4 plug in

- **P1 (click-to-call):**
  - Prepare: `attempts.create_attempt(mode="click_to_call", state="prepared", …)` then `attempts.reserve()`, which raises `409 calling_reserved` with the collector, case or extension.
  - Recheck everything (DNC via `calling_blocks`, `is_blocked`, callbacks, closed or paid, archived or locked, scope, `windows.status`, ownership via `extensions.active_for_user`), then `transition("authorized")`, `issue_dispatch_token(…, pbx_epoch_uuid=<current epoch uuid>)`, `bind_channel(id, epoch)`, and send `originate.extension_first_fields(...)` on the calling service's session. Its fields drove a full allowed call on the test PBX.
  - Project `call_events`/`call_legs` into state transitions: bridged only with bridge membership of both legs. Reconcile `uncertain` with `list_channels()` and release through `release_source='reconciled'`.
  - The AGI `authorize()` is where P1 adds its dispatch rechecks.
- **P2 (campaigns):** `call_attempts.campaign_id` and `mode='campaign'` already exist, the reservation indexes give one collector and one case, and the lists filter by `campaign_id`. It adds its own tables (0054–0055).
- **P3 (softphone):**
  - The WebRTC test extensions 7001–7005 are at `wss://localhost:8089/ws` (self-signed; Playwright/Chromium on the compose network can use `wss://pbx-test:8089/ws`).
  - `calling_preferences` holds auto-answer and devices. `user_extensions` decides who may receive credentials.
  - The test PBX has one shared SIP password; per-user short-lived credentials are P3's design. Host-browser media needs published RTP ports or `external_media_address` (open item 4).
- **P4 (evidence and recordings):** the CDR rows and recordings carry `cawp-<attempt>` / `cawp:<attempt>`, `call_legs` gives channel-level attribution, and the recording path follows the Issabel layout under `RECORDINGS_ROOT`. `/cdr*` is unchanged.

## 11. Tests

```
docker compose --profile pbx-test up -d pbx-test
CALLING_ENABLED=1 CALLING_DATABASE_URL=postgresql+psycopg2://postgres:postgres@test-db:5432/cawp_test_call0 \
    docker compose --profile pbx-test up -d calling
docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call0 api-test tests/test_calling_*.py
```

| File | Tests | What |
|---|---|---|
| `test_calling_windows.py` | 18 | Riyadh boundaries (08:59:59 closed, 09:00 open, 16:59:59 open, 17:00 closed); UTC vs Riyadh day and weekday; overnight and week wrap; unions; holidays; validation; `GotoIfTime` ranges |
| `test_calling_config.py` | 20 | real-PBX refusals (10.10.0.220, other hosts, test secrets, plaintext); the Originate builder; the dialplan gate structure (AGI via `GLOBAL()`, every check jumps to deny, options exactly `ibUL`); the pbx-test file equals the generator (host run) |
| `test_calling_ami_protocol.py` | 16 | framing, split UTF-8, repeated fields, poisoning, limits, action encoding and injection |
| `test_calling_ami_session.py` | 24 | fake AMI server: login and bad secret, out-of-order correlation, stray ActionIDs, the allowlist, action timeout without replay, event order, OriginateResponse as an event, consumer failure, stall and overflow, garbage, drops, heartbeat (missing or wrong Pong), `CoreShowChannels` (including a count mismatch), identity with a missing epoch, verified TLS and a wrong host name, supervisor reconnect with the same epoch and restart detection, PBX down |
| `test_calling_migrations.py` | 10 | every guard in section 8, including "no timeout release" and `CL004` |
| `test_calling_api.py` | 30 | manual log (note, follow-up, audit, unknown duration, validation, scope, locked or archived, replay); promise-discussed creates none; the agreed promise goes through the service (rolled back when the service refuses); wrong number blocks the contact (default replacement rule); DNC blocks the case (reused, release rules); PBX wrap-up (reservation released, one per attempt, revision, replay, states, evidence rule, uncertain refused, others); lists (scope, paging, filters, Riyadh dates, number masking); preferences; ownership (one owner, one extension, mirror, never moved while in use, legacy drift and adopt); windows |
| `test_calling_agi.py` | 20 | allow (gate cleared first, ALLOW last, durable before publish); one-shot; 4 concurrent requests give exactly one allow; 14 deny reasons; a terminal attempt; a stalled client; the peer allowlist |
| `test_calling_service.py` | 9 | classification (a forged UserEvent is not attributed, unrelated calls ignored, epoch-scoped dedupe); legs and roles; a lost session gives uncertain with reservations kept; a PBX restart gives `pbx_epoch_changed`; a DB outage buffers and then records an overflow gap; the DB down at session start; **two restarts release no reservation**; disabled mode; health |
| `test_calling_live_pbx.py` | 10 | **live test PBX:** AMI login, epoch, heartbeat and TLS; minimal privileges refused by the PBX; wrong secret; Originate to the simulated agent without a token → agent answered → **denied, no customer leg**; busy and unanswered agents; the **registered pjsua client (7101) answers**; **AGI end to end**: allow, customer answered, both legs in one bridge, recording path, legs and evidence in the DB, replay denied with no second Dial; a busy customer; a wrong-epoch token denied; the P1 Originate builder |

Results:
- Calling tests: **156 passed, 1 skipped**. The skipped check is equal when run on the host.
- Whole suite with the prefix: **929 passed, 1 skipped** with the test PBX and `calling` up; **919 passed, 11 skipped** with them stopped (about 3 min).
- When the test PBX is down, the live file skips with "test PBX not reachable at pbx-test:5038 - start it with …". The AGI end-to-end tests skip unless the `calling` service is enabled, connected and writing into this test database.
- Also run by hand: `python -m calling.verify` (all PASS, including a live deny-path call) and `pbx-verify.sh` (33 PASS, 2 expected WARNs) against the test PBX. `pbx-epoch.sh` refuses symlinks and files it did not write. A PBX rebuild was detected as a restart.

## 12. Open items

1. **Integration for the lead:**
   - add 0048–0051 to the `db/README.md` migration list (not my file);
   - drop the `profiles:` line of `calling` at the production cutover;
   - `.env.docker` needs nothing for P0.
2. **Number format towards the trunk:** the dialplan dials `+E.164` directly on the trunk. If the carrier needs `9665…` or `05…`, the generator needs a reviewed rewrite option. This is decided at commissioning (runbook §6 and §16).
3. **Issabel specifics are unverified** until the operator runs the runbook: the Asterisk version, whether `http.conf`/`manager.conf` are generated, the `globals_custom.conf` include, how Asterisk is started (for the epoch hook), and `userfield` logging in the MySQL CDR.
4. **Host-browser WebRTC on the test PBX:** only 8089 is published. Two-way media from a browser on the macOS host needs published RTP ports and `external_media_address`. Playwright inside the compose network does not. This is P3's call.
5. **Locked or archived case with a live call:** the wrap-up is refused (legacy note rule), so the reservation stays until the case is unlocked or P1 reconciliation handles it. It needs a product decision.
6. **Recordings in the test PBX are header-only** (the simulated customers send no RTP). Real audio is proven only at commissioning (gate 12).
7. **The attempt state is not projected from evidence yet** (P1), and `call_events` has no retention job yet.
8. **Server-side hang-up is not possible** with the minimal AMI privileges (decision 7). If P1 or P2 needs it, it is a reviewed privilege change.
9. **Holidays are enforced by the app only** (`GotoIfTime` has no year).

## 13. Containers and images

- Left running for the lead: `cawp-pbx-test` (about 60 MB) and `cawp-calling` (about 70 MB, `CALLING_ENABLED=1`, writing into the test database `cawp_test_call0`, which pytest drops after each run; the service then buffers and retries). I stopped both after the final run; start them with the commands in section 11.
- Built images: `cawp-pbx-test:20.11.1` and `old-calling`. The temporary probe image `cawp-pbx-probe` and the pulled `alpine:3.20`/`3.21` images were removed.
