# Alitqan: message campaigns (play a recorded message, 1 = agent, 2 = close) — plan for approval

Status (2026-10-25): **approved and built** (app: migration 0088, `services/calling/message_campaign.py`,
`message_dialplan.py`, `message_audio.py`, the AGI scripts; tests `tests/test_message_campaigns.py`);
PBX install and live test: §7. Requested by the customer:
"create campaigns with an auto message played to the client when they pick up, instead of
transferring the call; press 1 to transfer to an agent, 2 to close". Decisions taken with the
customer (2026-10-25): an end date/time (built: migration 0087), the number of simultaneous calls
set per campaign (default 5, max 20), the client's own campaign list, and when 1 is pressed and no
agent is free: hold music up to 60 s, then a "we will call you back" message, hang up, the client
leaves the campaign and a note records it.

## 1. How a message call works

1. The campaign (kind `message`) dials up to N clients at once (N = the campaign's setting), from
   the same pool as today (filters or customer list, every exclusion: do-not-call, paid, any
   payment, answered in this campaign, removed by hand, calling window, end time).
2. The PBX dials the client's number **once** on the first reachable Mobily trunk with the trunk's
   caller ID (the same rules as click-to-call: no second SIM after a failure), 60 s ring (30 s until 2026-10-29).
   A one-shot token checked by the app's FastAGI hook gates the dial: no token, no call.
3. Client answers → the uploaded message plays (once, then once more if no key is pressed).
4. Keys:
   * **1** → the app picks a free campaign member (ready, not in a call, longest idle) and the PBX
     rings that member's extension (desk phone or softphone); the client hears hold music. The
     member answers → client and member talk; the call is recorded; the member wraps it up like
     any campaign call (the note, the promise, the "promise moves the client" rule).
     No member free or none answers within 60 s in total → "we will call you back" message,
     hang up, the client **leaves the campaign** and a note is recorded.
   * **2** → a short goodbye (optional upload), hang up; note recorded; the client leaves the campaign.
   * no key → hang up after the second play; note recorded ("heard the message, no choice");
     the client leaves the campaign (they answered; the 2026-10-24 rule).
5. No answer / busy / switched off → nothing is recorded on the case; the client stays in the
   pool for a later attempt (max attempts per day, cooldown).

"Answered" is only the PBX's evidence that the client's leg answered (the GSM gateway answers only
on a real answer, live-tested 2026-10-21 gate 11). Unknown stays unknown: a call whose end the
service did not see is *uncertain* and reconciled, never counted.

Note on the project rule "click-to-call is extension-first": message campaigns dial the client
first by design (the customer's request). They are a separate campaign kind with their own
restricted dialplan; click-to-call and agent campaigns stay extension-first and unchanged.

### 2026-10-29 additions (migration 0089)

* **Times the message plays** (1..5, default 2) when no key is pressed: `campaigns.message_plays`, sent on
  the Originate as `COLLECTIONS_MSG_PLAYS_MAX` and re-checked by the dialplan (anything else = 2).
* **Time on the line**: `message_notes.listened = {seconds, status_id, sub_status_id}`. A client who stays
  on the line more than `seconds` after answering gets that status note instead of the result's own,
  whatever key they pressed (customer's choice: always); measured at the result, else at the call's end.
* A promise status saved by the member a "1" transferred the client to moves the client to that member
  (campaign_rules.claim_on_message_promise; `campaign_case_assignments.message_call_id`).

### 2026-10-31 addition (migration 0090): the outbound number

A tab on the campaign page picks the number the client sees (customer's list of 118, or automatic). All
82 Mobily trunks are the same peer (same host, no per-number credentials) and one number already carries
26+ calls at once, so the number is the call's caller ID and the trunk choice does not change. The FastAGI
gate publishes it (`COLLECTIONS_MSG_CID`, cleared first, re-checked by the dialplan; never from the
Originate), and the `dialing` event reports what the PBX used (`message_calls.caller_id`).
Whether Mobily accepts the 39 numbers with no PBX trunk is NOT yet proven: confirm with a test call.

## 2. Messages (audio)

* Uploaded on the campaign form: **message** (required), **we will call you back** (required when
  option 1 is on), **goodbye** (optional). WAV or MP3, at most 2 minutes, 10 MB.
* The app converts them to the PBX format (8 kHz mono 16-bit WAV) and sends them to the PBX through
  the existing provisioning connection (HTTPS + token, the vhost already used for extensions).
* Stored on the PBX as `/var/lib/asterisk/sounds/cawp/<campaign>-<kind>.wav` (root:asterisk 0640).
  Hold music: the PBX's `default` class.

## 3. Notes recorded on the case

The campaign form asks for the note status (and sub-status) to use for each recorded result:
"no agent, call back", "closed (2)", "no choice". Author = the campaign's creator, contact method
= phone call, text = what happened ("استمع العميل للرسالة واختار ..."), with the call's number.
The legacy last-comment trigger and the campaign rules (stops_campaigns, ...) apply as for any note.

## 4. PBX changes (all reversible; no restart)

| # | Change | Reload |
| --- | --- | --- |
| 1 | Backup of `/etc/asterisk` and the provisioning files | none |
| 2 | Generated dialplan `/etc/asterisk/collections-message.conf` (+ one `#include` in `extensions_custom.conf`): contexts `collections-message-dial` (token check via the FastAGI hook, trunk selection, caller ID, one Dial), `collections-message-ivr` (play, read the key, ask the app for a free member, ring them with hold music, 60 s limit, messages), event hooks like the click-to-call ones. Reachable only from the app's Originate | **`dialplan reload`** |
| 3 | Provisioning script: new operation `sound put <campaign> <kind>` (WAV header and size checked, written atomically under `/var/lib/asterisk/sounds/cawp/`) and `sound delete`; vhost route `POST/DELETE /v1/sounds/...`; sudoers line | `httpd` graceful reload (Issabel UI unaffected) |

Nothing else changes: no `sip.conf`, trunks, routes, AMI rights (the `cawp-calling` user already has
`originate`), firewall or Issabel dialer.

## 5. App changes

* Migration: `campaigns.kind` (`agent` default | `message`), `max_concurrent` (1..20),
  `message_keys` (option 1 on/off), the three note statuses; `campaign_sounds`;
  `call_attempts.mode` accepts `message`; message results on the attempt (`ivr_key`, `ivr_result`).
* Calling service: a message dispatcher (paced by `max_concurrent`, the end time, the calling
  window), the Originate to the dial context, FastAGI scripts `collections-message-authorize`,
  `collections-message-agent` (picks the free member), `collections-message-result`; evidence and
  reconciliation for the client leg and the member leg.
* Screens: the campaign form (kind, messages upload and preview, number of simultaneous calls,
  option 1, note statuses), the live view (calls in progress, messages heard, 1/2/no choice,
  transferred, waiting), the Called tab with the key pressed.
* Tests next to the campaign tests; then a live test on the PBX with the test mobile and a test
  extension (as for click-to-call, gates: answered + 1 + member answers; 1 + nobody free; 2; no
  key; no answer; switched off; the end time; the pace limit).

## 6. Risks and limits

* The GSM gateway's capacity is shared with desk phones and click-to-call (22 calls at once
  measured at today's peak): keep N small at first (default 5).
* Calls that play a message cost airtime even when nobody presses 1.
* Some recipients may consider recorded calls intrusive; the content and timing are the customer's
  responsibility (calling hours apply).

## 7. Install and live test

PBX (after a backup of `/etc/asterisk`, `/var/www/cawp-provision`, `/etc/httpd/conf.d/cawp-provision.conf`):
`/etc/asterisk/collections-message.conf` (generated from the Alitqan policy: Mobily18..26 = Issabel
trunks 2..10, 60 s ring (30 s until 2026-10-29), 3600 s limit, 60 s wait for a member, 20 s member ring, recording, 24/7) +
`#include collections-message.conf` in `extensions_custom.conf`; `COLLECTIONS_MSG_AGI=agi://127.0.0.1:4573`
in `collections-globals.conf`; `/var/lib/asterisk/sounds/cawp` (asterisk:asterisk 0750); the
provisioning `index.php` (PHP 5.4 variant) and the vhost (`/v1/sounds/...` rule, PUT, 4.2 MB limit
on that path only); `apachectl configtest` + `systemctl reload httpd`; one `dialplan reload`.

Live test (the test mobile 0563287135 on a test case in the test contract, a test member at a desk
phone; the campaign's pace 1):

| # | Test | Pass when |
| --- | --- | --- |
| 1 | Upload the three messages | converted, "installed" on the PBX (`/var/lib/asterisk/sounds/cawp/<id>-*.wav`) |
| 2 | Start; the mobile rings with the trunk caller ID; answer; listen; press 2 | the message plays, then goodbye; result `closed`; the note recorded; the client not called again |
| 3 | Answer, press nothing | the message twice, then hang up; result `no_choice` |
| 4 | Answer, press 1, the member is ready | the member's phone rings (the client hears hold music), the member answers, both talk; result `transferred`; the member's screen shows the client |
| 5 | Press 1, no member ready | hold music ~60 s, the callback message, hang up; result `no_agent`; the note; the client leaves the campaign |
| 6 | Do not answer / reject | `no_answer` / `busy`; the client stays for a later call |
| 7 | The end time passes during the campaign | no new dial; the call in progress finishes; the campaign stops |
