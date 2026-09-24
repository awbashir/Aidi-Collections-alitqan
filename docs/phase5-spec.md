# Phase 5 spec: human calling, campaigns, in-app softphone, PBX evidence

This spec is the source of truth for the Phase 5 agents. The rules in `docs/PROJECT-NOTES.md` apply: §0 (especially rule 4, "PBX = Issabel, keep `/cdr*`", and rule 5, "build our own softphone; never use cloudsip.app code"), the catalogue in §2.9, the softphone plan in §3 (read it in full), and the §4 conventions and decisions. The rebuild's calling design and evidence are in `docs/reference/rebuild-human-calling.md`, `rebuild-issabel-dialer.md`, `rebuild-voice-roadmap.md` and `rebuild-human-dialplan-policy.json`. Port the **concepts** into Flask/PostgreSQL and the Phase 2 worker, not the Node runtime. More rebuild source, read-only, is available through `gh api repos/awbashir/Aidi-Collections/contents/<path>`, e.g. `src/platform/ami-*.js`, `human-call-agi.js`, `human-dialplan.js`, `scripts/test-asterisk-postgres.js`, `tests/fixtures/asterisk`.

## Hard rules

1. **The real Issabel PBX is never contacted** from development or tests. Everything is proven against a **local Asterisk test PBX in Docker** (compose profile `pbx-test`, pinned image), configured the way the Issabel commissioning runbook will configure production.
2. **Extension-first.** The server authorizes an attempt, then originates to the **agent's extension**, and only then dials the customer through a restricted dialplan context. The browser never dials an arbitrary number. An AMI "Success" response means *submitted*, never connected.
3. **Evidence, not inference.**
   - A conversation is proven only by PBX events: the customer leg answered **and** bridged with the agent leg, correlated by attempt id (`UserEvent CollectionsCall` + channel/bridge events).
   - Two answered legs alone are not a conversation.
   - A lost AMI connection or an ambiguous topology leaves the attempt **uncertain** until it is reconciled.
   - A restart never auto-releases an active reservation.
4. **Recheck at dispatch.** Do-not-call, blocked contact (Phase 1 `is_blocked`), a future callback, closed/paid, archived/locked, scope, calling window, an existing reservation, and extension ownership. Queue presence never authorizes a call.
5. **Wrap-up** is one reviewed transaction: outcome (connected, no answer, busy, promise discussed, dispute, wrong number, do-not-call), a note, an optional callback, and an **explicitly agreed** promise.
   - "Promise discussed" never creates a promise.
   - Wrong number blocks that contact.
   - Opt-out / do-not-call blocks the case for calling.
6. **Credentials.** SIP passwords are issued by the API to the extension's owner only: short-lived, never stored in the browser's storage, never logged. AMI and AGI secrets live in the server env only.
7. **Recordings.** Issabel/Asterisk (MixMonitor) is the source of truth. Access goes through the existing `/cdr/recording` under the recording keys, now **scoped**: a recording is only served when its CDR attributes to a case in the caller's scope, or to the caller's own extension.
8. **Simulation** exists only in the test PBX or an explicit "simulated" mode that is labelled everywhere and can never dial a real number.

## Streams

| Stream | Scope | Migrations | Test prefix |
|---|---|---|---|
| **P0: foundation** (first, alone) | local Asterisk test PBX (`docker-compose` profile `pbx-test`: PJSIP with WSS/DTLS/ICE for WebRTC extensions, AMI over TLS or loopback, a loopback "trunk" that simulates customers answering, busy, no answer and hanging up by number pattern, MixMonitor, CDR to a local MySQL or CSV); **`docs/issabel-commissioning.md`**, the production runbook (AMI user and privileges, http.conf TLS/WSS, PJSIP or chan_sip WebRTC endpoints for Issabel's version, dialplan contexts, the AGI authorization hook, recording, CDR, coturn, firewall, verification checklist); a persistent AMI client (auth, keepalive, event stream, action/response correlation, reconnect with epoch); the data model (extensions and ownership, calling preferences and windows, call attempts, reservations, legs/evidence, wrap-ups, do-not-call); manual call log + wrap-up v2 API; the `calling` service in docker-compose (long-lived AMI owner + FastAGI server) | 0048–0051 | `cawp_test_call0` |
| **P1: click-to-call** | dispatch authorization (a one-shot token consumed by the AGI check), extension-first originate, event → evidence projection (agent leg, customer leg, bridge, hangup cause), uncertainty holds and reconciliation (channel inventory), late-evidence handling, attempt lifecycle API; end-to-end tests against the test PBX | 0052–0053 | `cawp_test_call1` |
| **P2: campaigns** | team pool, roster, readiness heartbeat leases, one reservation per collector/case, progressive next-call (automatic/manual), wrap-up delay, pause/drain/stop, dispatch rechecks, supervisor view; end-to-end against the test PBX | 0054–0055 | `cawp_test_call2` |
| **P3: softphone and SIP credentials** | `POST /v2/me/sip-credentials` (owner of an extension only, short TTL); softphone service module in the frontend (JsSIP, MIT, pinned npm); features per PROJECT-NOTES §3.2; auto-answer only for attempt-tagged calls; multi-tab ownership; device settings; diagnostics | backend part only if needed (0056) | `cawp_test_call3` |
| **P4: PBX evidence and recordings** | scoped `/cdr/recording`; reviewed PBX CDR spreadsheet import (a Phase 2 bulk kind) with source/case/leg attribution; RPT-23/25 linked to attempts where known | 0057 | `cawp_test_call4` |

- P1, P3 and P4 start after P0; P2 starts after P1.
- The frontend (calling workspace, campaign screens, calling admin, softphone UI) comes after the backend, apart from P3's softphone module.
- Every stream proves its behavior against the **test PBX**. Playwright can register the in-app softphone to the test PBX over WSS using Chromium's fake media devices, and check two-way RTP (for example, audio level on both legs).

## Permissions (screen-key rule where a legacy screen exists; otherwise new keys)
- `calls`: call log and my calls.
- `calls_make`: click-to-call and the softphone.
- `calls_manage`: calling admin, extensions and windows.
- `campaigns`: see campaigns.
- `campaigns_manage`: run and pause campaigns.

The lead registers them. Recordings keep `clints_callsRecords_download` / `_play`.

## Decisions taken for this phase (the lead's, following established rules; recorded in the notes)
- Softphone SIP library: **JsSIP** (MIT), pinned from npm. No code from cloudsip.app.
- Dialer mode: **progressive only** (one call per ready collector). No predictive or speculative dialing.
- The AI voice route (OpenAI Realtime SIP) is **out of scope**. Phase 6 only covers the training lab and quality review.
- The test PBX image is pinned by digest. The production Issabel configuration is documentation plus verification scripts that the operator runs; nothing is pushed to the PBX automatically.
