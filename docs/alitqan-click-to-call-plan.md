# Alitqan: click-to-call and campaigns on Issabel 4 / Asterisk 11 — plan for approval

Status (2026-10-21 22:28 Riyadh): **PBX side installed, app connected, dispatcher OFF** until the
live test (§5). Customer decisions: calls allowed 24/7; desk-phone SIP transfers accepted for now;
live test with extension 222 (Fahad Alawlaqi, Yealink T46S) and the authorized test number
0563287135. Code: commit `080a415` + the review fixes (§6), deployed with migrations 0084/0085.

Done: PBX backup `/root/cawp-backup-alitqan-c2c-20261021-222758`; `collections-human.conf` (100
lines) and `collections-globals.conf` installed and included, one `dialplan reload` (0 active calls
before and after); the AGI hook reached over the SSH reverse forward (PBX `127.0.0.1:4573` → the
calling service; a probe was denied as it must be); the app's calling window 24/7; `calling.verify`
all PASS apart from the expected call-control WARNs (`call` write for listen/join) and "no event seen
yet". Next: switch the dispatcher on and run §5 with the tester.

Live test (dispatcher ON, campaigns off): gate 1 **passed** (deny `token_missing`, no customer leg).
Gate 2 first failed: the GSM gateway answered the customer INVITE with **SIP 604 "Does Not Exist
Anywhere"** because the call carried the agent's bare extension (`222`) as caller ID, where
IssabelPBX's own route sends the user's `outboundcid` or else the trunk's `OUTCID_N`. Fix: policy
option `issabel_trunk_ids` (Mobily18..26 = Issabel trunks 2..10) makes the generated dialplan set
that same caller ID right before the one Dial; installed 2026-10-21 22:45 (backup
`/root/cawp-backup-alitqan-c2c-cid-20261021-224508`, 114 lines, one `dialplan reload`, 0 active
channels before and after). Gate 2 then **passed** (attempt `ee0a43b2`): authorized → dispatched →
customer_dialing → bridged → ended `agent_hung_up`, 22 s talk from the PBX, conversation evidenced;
CDR `outbound_cnum` = Mobily18's number, `userfield=cawp:<attempt>`, recording played through the
app (`/cdr/recording`, 359 KB WAV). Gate 3 **passed** (attempt `7171d666`): one customer leg on
Mobily18, `dial_end NOANSWER` after the 30 s ring, no bridge, attempt ended `customer_no_answer`. Gate 4 **passed** (attempt `bb09a66c`): the mobile
rejected, one leg on Mobily18, CDR `BUSY`, no bridge, attempt ended `customer_busy`. Gate 5 **passed** (attempt `2e475896`): 222 rang out
unanswered, attempt `failed` `agent_no_answer` after 30 s, no AGI decision, no customer leg in the
CDR, the reservation released by itself (`no_customer_contact`). Gate 6 **passed** (attempt `eaca3d57`):
bridged, the mobile hung up first, the customer leg ended before the agent leg (cause 16), attempt
ended `customer_hung_up`, 6 s talk from the PBX. Gate 7 **passed** (attempt `09612296`; a first
try `c14e052a` restarted only after the call had ended and does not count): `alitqan-calling`
restarted 5 s after the bridge; the call stayed up and bridged on the PBX; the attempt went
`uncertain` (`calling_service_stopped`) and, 10 s after the hang-up, was reconciled to `ended` with
the PBX's 60 s talk; the reservation stayed held until the wrap-up. Gate 8 **skipped** by the
customer's decision (calls are allowed 24/7; the window checks are covered by the unit tests).
Gate 10a **passed** (attempt `71f976ef`, test mobile switched off): one `Called SIP/Mobily18/…`,
the gateway answered SIP **480 Temporarily Unavailable** (chan_sip: circuit-busy, CONGESTION), no
other trunk tried, attempt ended `customer_congestion`, not answered. Note: Asterisk 11 writes this
call's CDR row as `ANSWERED` (the agent's leg answered); the Call monitor takes answered/talk of
`cawp:` calls from the app's attempt (not bridged), so it shows it unanswered. Gate 10b (invalid
number) **skipped**: no number known to be unassigned was available; 10a already shows one Dial and
no failover on a far-end failure. Gate 11 counted as **passed** by gate 3 (30 s ring, `NOANSWER`,
never answered or bridged). Gate 9 **passed** (campaign `4009c2c5`, manual mode, the test contract
with TEST-CALL-0001/0002, Fahad the only member; `CALLING_CAMPAIGNS_ENABLED=1` from 2026-10-21
23:06): call 1 (`afaa227b`, TEST-CALL-0001) and call 2 (`fc66c3bc`, TEST-CALL-0002) one at a time,
both bridged and recorded on Mobily18 with the trunk caller ID; "next" during each call and before
each wrap-up refused `409 call_in_progress` (one live attempt in the app); the second call only
after the first wrap-up; pause, then "next" refused `409 campaign_not_running`; stop; exactly 2
attempts, both reservations released.

**Result: every gate run passed** (8 and 10b skipped by decision). The dispatcher and campaigns are
ON. Left: archive the test cases TEST-CALL-0001/0002 and the test contract, delete the live-test
helpers `/root/c2c_*.py` and `/root/.fahad_token` on the app server.

## 1. How a call works (extension-first)

1. A collector clicks the phone icon on a case contact. The API checks scope, do-not-call,
   calling window, the collector's extension and one reservation per collector/case.
2. The calling service (the only AMI client) sends an AMI Originate to the collector's own
   extension (`SIP/4xx`, their desk phone or browser softphone). AMI "Success" = submitted only.
3. The collector answers. Only now the restricted dialplan runs on the collector's channel and asks
   the app (FastAGI hook) with a one-shot token. No ALLOW, no customer call.
4. The dialplan rechecks every gate output and dials the customer `05XXXXXXXX` **once**, on the first
   of Mobily18 … Mobily26 (the order of the Issabel route `Direct_outside`) whose SIP peer is up; it
   never tries a second trunk after a dial (a far-end "switched off" or "invalid number" must not
   ring the customer from another SIM). 30 s ring; the call is recorded and tagged
   `cawp:<attempt>` in the CDR.
5. "Connected" only from PBX evidence: the customer's channel answered **and** a Bridge Link of exactly
   the collector's and the customer's channels. Anything unclear stays *uncertain* for review.
6. Wrap-up on the case (note, follow-up, optional promise) releases the reservation.

Campaigns use the same path, one call at a time per collector (progressive, never predictive).

## 2. PBX changes (all reversible; no restart; one `dialplan reload`)

| # | Change | File | Reload |
| --- | --- | --- | --- |
| 1 | Backup of `/etc/asterisk` and the IssabelPBX tables | `/root/cawp-backup-alitqan-c2c-*` | none |
| 2 | The restricted outbound dialplan (generated, 100 lines, contexts `collections-human-outbound`, `collections-customer-created/answered/finished`, `collections-agent-finished`; reachable **only** by the app's Originate, never from a phone's dialing context) | `/etc/asterisk/collections-human.conf` (root:asterisk 0640) + one `#include` line appended to `extensions_custom.conf` | — |
| 3 | The AGI hook address and key: `COLLECTIONS_AGI_URL=agi://127.0.0.1:4573/collections-authorize`, `COLLECTIONS_AGI_KEY=<new secret>` | `/etc/asterisk/collections-globals.conf` (root:asterisk 0640) + one `#include` line in `globals_custom.conf` | — |
| 4 | Load 2 + 3 | — | **`dialplan reload`** (the same reload Issabel's Apply Config does; calls continue) |

Not changed: AMI (the `cawp-calling` user already has `originate,reporting,call`), `sip.conf`,
trunks, outbound routes, the Issabel dialer, the firewall, CDR settings (`userfield` already logged).

Why `127.0.0.1` for the hook: **the PBX cannot open connections to the app server**
(10.10.0.220 → 10.10.10.150 is blocked on the network; the other direction works). The app
server's existing SSH tunnel to the PBX (`alitqan-ami-tunnel`, key restricted to forwarding) also
carries a reverse forward of the PBX's `127.0.0.1:4573` to the calling service, so no network or
firewall change is needed. Alternative: open TCP 10.10.0.220 → 10.10.10.150:4573 on the LAN
firewall and use `agi://10.10.10.150:4573/...`.

## 3. App changes

* Deploy commit `080a415` (+ the review fixes), migration `0084`.
* `alitqan-ami-tunnel`: add `-R 127.0.0.1:4573:127.0.0.1:4573`.
* `/etc/alitqan/api.env`: `CALLING_PBX_EVENTS=legacy`, `CALLING_AGI_ENABLED=1`,
  `CALLING_AGI_BIND=127.0.0.1`, `CALLING_AGI_ALLOWED_PEERS=127.0.0.1`, `CALLING_AGI_SECRET=<same key>`,
  `CALLING_DISPATCH_ENABLED=1`, `CALLING_CONTEXT=collections-human-outbound`,
  `CALLING_MAX_CALL_SECONDS=3600`, `CALLING_RECONCILE_SECONDS=10`; campaigns
  (`CALLING_CAMPAIGNS_ENABLED=1`) only after the click-to-call gates pass.
* The calling window in the app (Calling settings) = the same hours as the dialplan.
* For the test: a test collector with `calls_make`, their extension assigned in the app, and a test
  case whose contact is the tester's own mobile number.

## 4. Decisions needed

1. **Calling hours** (Asia/Riyadh): which days and hours may the app call customers? The dialplan is
   generated from them and the app enforces the same (holidays are app-only). Placeholder: 24/7.
2. **Desk-phone transfers**: the agents' phones may transfer calls themselves (`SIP Transfer mode:
   open`). A click-to-call customer transferred from the phone leaves the app's control (the app then
   marks the call for review). Options: accept it (no change), or close SIP transfers
   (`allowtransfer=no`, `sip reload`), which also stops agents transferring their ordinary calls
   from the phone. Recommended: accept for now, decide after the pilot.
3. **Hook path**: the SSH reverse forward (recommended, no network change) or a LAN firewall rule.
4. **Test people and numbers**: a tester at a desk-phone extension and an authorized test mobile
   number (never a customer's).

## 5. Live test plan (before any real collector)

Done together: the tester at the desk phone and the test mobile; me watching the app, AMI evidence
and the CDR. Each gate needs a pass before the next.

| # | Test | Pass when |
| --- | --- | --- |
| 0 | `python -m calling.verify --driver SIP --pbx-events legacy --trunk Mobily18,…,Mobily26` (read-only) | every line PASS (epoch, AMI rights, contexts, globals, trunks) |
| 1 | **Deny path**: `calling.verify --place-deny-test <test ext>`; the tester answers | the hook refuses (`call_agi_decisions` deny), **no customer leg**, the call ends |
| 2 | Click-to-call to the test case: desk phone rings first, tester answers, then the mobile rings; answer; talk 30 s; hang up from the desk phone | live view: authorized → dispatched → agent_answered → customer_dialing → customer_answered → **bridged** → ended; wrap-up releases the reservation; recording plays in the app; CDR `userfield=cawp:<attempt>` |
| 3 | Mobile not answered | `dial_end NOANSWER`, attempt ends unanswered, no bridge |
| 4 | Mobile rejects / busy | `BUSY`, no bridge |
| 5 | Desk phone not answered | attempt `failed` (`agent_no_answer`), no hook call, reservation released |
| 6 | Mobile hangs up first | `customer_ended`, then `agent_ended` |
| 7 | Calling service restarted during a call | the call continues on the PBX; the attempt becomes `uncertain` and is reconciled; the reservation is kept until then |
| 8 | Outside the calling window (with a temporary 1-minute window) | refused by the app; a forced call is denied by the PBX |
| 9 | Campaign: 1 collector, 2 test cases | one call at a time, next call after wrap-up, pause and stop work |
| 10 | Test case with a **switched-off** mobile, then an **invalid** number | one Dial only, on one trunk (CDR and AMI: no second customer leg); the attempt ends unanswered |
| 11 | Mobile rings out without being picked up (30 s) | `NOANSWER`, **never** "answered/bridged" (proves the GSM gateway answers only on a real answer) |

**Stop at once** (rollback §7) if a customer number is ever dialled before the agent answered, or
without an ALLOW, or if any Issabel-dialer call is affected.

## 6. Code review of `080a415`

An independent review (read-only, simulated Asterisk 11 event sequences) found, and the fixes
address before anything is deployed:

| # | Finding | Fix |
| --- | --- | --- |
| 1 | The AGI hook could allow the customer dial from a channel other than the one the app originated, if the OriginateResponse binding was late or refused | allow only when the AGI channel IS the agent leg bound from this attempt's OriginateResponse (waiting up to ~2.5 s for it), deny otherwise |
| 2 | Trunk failover on CHANUNAVAIL/CONGESTION can re-dial the same customer on up to 9 SIMs (chan_sip reports far-end 480/404/503 that way) | pick the first trunk whose SIP peer is up, dial once, never fail over after a dial |
| 3 | A call-control transfer/hold on Asterisk 11 (masquerade) would end the attempt with a wrong duration | a `<ZOMBIE>` hangup is not a leg hangup; a masquerade sends the attempt to review |
| 4 | Migration 0084 would lock `call_legs` (index without CONCURRENTLY, CHECK validation) | index in its own concurrent migration; constraints NOT VALID + VALIDATE |
| 5 | Held early events could outlive their TTL; an Unlink without a Link ended a call as "not bridged" | TTL on every key; a bridge seen only at its end is *uncertain*, not "not bridged" |

Checked and sound: the ActionID correlation against the Issabel dialer's own events and sessions,
unexpected/`<null>` frames, held-event replay, restart recovery, the generated dialplan on
Asterisk 11, and no regression of the PJSIP mode.

## 7. Rollback

1. App: `CALLING_DISPATCH_ENABLED=0` (and campaigns off), restart `alitqan-calling`: no Originate,
   the hook answers nothing, every customer Dial is denied.
2. PBX: remove the two `#include` lines, `dialplan reload` (or restore from the backup).
3. Attempts and reservations stay in the database (never deleted); uncertain ones are resolved by a
   supervisor.
