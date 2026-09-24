# Phase 5 P3: in-app softphone and SIP credentials

Stream P3 of `docs/phase5-spec.md`, on branch `phase-5-calling` (not committed). It builds on P0 (`review/phase5-foundation.md`) and P1 (`review/phase5-click-to-call.md`).

- The real Issabel PBX (10.10.0.220) was **never contacted**.
- No CONNXTA/cloudsip.app code, file, asset or link was used. The softphone was written from scratch around **JsSIP 3.13.8 (MIT)**, pinned exactly in `package.json`.

## 1. Summary

| Piece | Where | State |
|---|---|---|
| `POST/GET /v2/me/sip-credentials`, `POST /v2/me/sip-credentials/revoke` | `CAWP-API/api/calling_sip.py`, `services/calling/sip.py`, `sip_config.py` | 20 API/DB tests |
| Attempt tag on the agent leg (`X-CAWP-Attempt`) | `services/calling/sip_tag.py` | unit test + live in the browser run |
| Data model: HA1-only credentials and the PBX view `pbx_ps_auths` | `db/migrations/0056_sip_credentials.sql` | applied by the test harness. **The lead applied it to cawp-db at 08:44 UTC; its checksum matches this file, which was not edited after that** |
| WebRTC test PBX (Asterisk 20.11.1): realtime PJSIP auths from PostgreSQL | `pbx-webrtc-test/`, compose profile `pbx-webrtc-test` | live digest test (1) + browser run |
| Browser end-to-end runners (the e2e API, and Chromium in the compose network) | compose services `softphone-e2e-api`, `softphone-e2e-browser`, `pbx-webrtc-test/e2e/*` | used by the verify script |
| Softphone service module | `CAWP-frontend/src/services/softphone/` | tsc 0 / eslint 0 in the new files |
| Softphone UI (header chip, docked panel) and shell integration | `CAWP-frontend/src/components/softphone/`, 5 layouts | 175 `sp.*` keys (ar + en) |
| Browser verification | `CAWP-frontend/scripts/verify-phase5-softphone.cjs` | **78/78** (203 s); 22 screenshots |
| Backend tests | `CAWP-API/tests/test_calling_sip.py`, `test_calling_sip_live.py` | **21 passed**. Whole suite: **1083 passed, 5 skipped** (19 deselected: the P0/P1 live PBX files) |

## 2. Credential design

### 2.1 Decision

Each credential is a per-issue random password. The PBX checks it through a **realtime PJSIP auth backend in PostgreSQL**. The database stores only HA1, and the expiry is enforced by the PBX lookup itself.

I chose this over "a long-lived per-extension secret delivered just in time" because the other option cannot make the TTL real. With a long-lived secret, `expires_at` would only be a client refresh hint: the PBX would accept the secret forever, and a leaked browser memory dump would stay valid until someone rotated it by hand. With this design, both halves are real on the test PBX:

- a superseded, expired, revoked or released credential is refused by Asterisk with `401` (proven live);
- the database holds nothing password-equivalent after revocation or expiry.

### 2.2 How it works

1. **Issue:** `POST /v2/me/sip-credentials` (key `calls_make`).
   - **Who is refused:**
     - A caller without an active `user_extensions` row gets `409 no_extension`. The legacy `users.extension` column alone never authorizes.
     - An inactive user gets `403 actor_inactive`.
     - A server without `SIP_WSS_URL` answers `503 softphone_not_configured`.
     - More than `SIP_ISSUE_LIMIT` (30) issues per user in 10 minutes gives `429 too_many_credentials`.
   - **In one transaction:**
     - advisory locks on the user and the extension, and `FOR UPDATE` on the ownership row;
     - clear the HA1 of every expired credential (anyone's);
     - revoke (`superseded`) the current credential of this extension (whoever held it) and any other current one of this user, clearing their HA1;
     - insert the new row;
     - audit `calling.sip_credentials.issue`, recording the credential id, extension, realm, expiry, TTL, the superseded ids and how many HA1 were cleared. **The password and the HA1 are not in the audit.**
   - The password is `secrets.token_urlsafe(24)` (192 bits).
2. **Response (201, `Cache-Control: no-store`):**

   ```json
   {"credential_id": "…", "extension": "7001", "sip_uri": "sip:7001@pbx-webrtc-test", "authorization_user": "7001",
    "realm": "cawp-webrtc-test", "password": "<32 chars, shown once>", "display_name": "Agent One",
    "wss_url": "wss://pbx-webrtc-test:8089/ws", "ice_servers": [],
    "issued_at": "…", "expires_at": "…", "ttl_seconds": 90, "refresh_after_seconds": 62, "register_expires": 60,
    "auto_answer": {"header": "X-CAWP-Attempt", "enabled": true},
    "dial_policy": {"internal_pattern": "^(7[0-9]{3}|600)$", "echo_test_extension": "600", "external": "click_to_call_only"},
    "storage": "memory_only"}
   ```

   - `ice_servers` holds STUN as configured, plus TURN with a **coturn REST credential**: username `<expiry>:<id>`, credential base64(HMAC-SHA1(`SIP_TURN_SECRET`, username)). No long-lived TURN secret reaches the browser.
   - `dial_policy.internal_pattern` comes from `SIP_INTERNAL_DIAL_PATTERN`. A pattern that would match an external number (for example `05…`, `9665…` or `+966…`) is replaced by the default `^[0-9]{3,6}$`.
3. **Not idempotent, on purpose.** The `@idempotent` store keeps the response body, and here the body is the secret. A repeated POST issues a new credential and supersedes the previous one, so a retry is harmless. An `Idempotency-Key` header is accepted and ignored; a test proves nothing is stored for it.
4. **At rest:** `sip_credentials` (0056) stores `ha1 = MD5("ext:realm:password")`, which is what PJSIP `auth_type=md5` needs.
   - HA1 is password-equivalent for that realm, so it is kept only while it can be used. A guard trigger allows exactly two one-way changes: revoking the row (which must clear the HA1) and clearing the HA1 of an expired row. Identity and expiry are immutable, a current credential is never deleted, and a new row must be current and carry an HA1.
   - One current credential per extension (partial unique index).
5. **What the PBX reads:** the view `pbx_ps_auths(id, auth_type='md5', username, realm, md5_cred)`. It returns a row only when:
   - the credential is not revoked;
   - `expires_at > now()` on the **database clock**;
   - its `user_extensions` row is still active and is the same user and extension;
   - the user `is_active`;
   - it was issued after `users.sessions_valid_after`.

   So expiry, extension release, user deactivation, and a role, password or "sign out everywhere" change (0036 moves `sessions_valid_after`) all stop the credential **at the PBX's next authentication**, with no job and no push. The PBX role needs `SELECT` on the view only. The migration grants it when the role `cawp_pbx_realtime` exists; a test checks the role cannot read `sip_credentials`.
6. **PBX:** `sorcery.conf` maps `auth` objects to `realtime,ps_auths` first, then to the config file.
   - `pjsip.conf` holds a "locked" auth per WebRTC extension, whose HA1 is random per start and never logged (only the Asterisk CLI can show it). A missing or expired credential therefore gets a clean `401` instead of an auth-lookup error.
   - The endpoints and AORs stay in the config file (`webrtc=yes`, `allow_transfer=no`, `max_contacts=1`, `remove_existing=yes`), exactly as in `pbx-test`.
7. **TTL and refresh:** `SIP_CREDENTIAL_TTL_SECONDS` (default 600, clamped 60..3600) and `SIP_REGISTER_EXPIRES` (default min(300, TTL)).
   - The softphone refreshes at 70% of the TTL.
   - It also fetches again on every WebSocket reconnect, and once after a `401/403`.
   - Each refresh calls `ua.set('password')` and then `ua.register()`.
   - In-dialog requests (hold, BYE, REFER) keep working across a rotation, because JsSIP always uses the current credential.
8. **Revoke:** `POST /v2/me/sip-credentials/revoke` sets `user_revoked`, clears the HA1 and is audited. `GET /v2/me/sip-credentials` returns the status (configured, extension, current credential id and expiry, `active`) and never the secret.
9. **Never logged:** no route logs bodies (checked in `app.py` and `core/*`); gunicorn logs the request line only. A test captures every log record during an issue and finds no password in it.

### 2.3 Settings (API host environment; nothing is in `core/config.py`)

- `SIP_WSS_URL` (required, `wss://` only)
- `SIP_DOMAIN`
- `SIP_REALM` (default `cawp`; it must be the realm the PBX challenges with, and the realm is written into every row)
- `SIP_CREDENTIAL_TTL_SECONDS`, `SIP_REGISTER_EXPIRES`, `SIP_ISSUE_LIMIT`
- `SIP_STUN_URLS`, `SIP_TURN_URLS` + `SIP_TURN_SECRET`
- `SIP_INTERNAL_DIAL_PATTERN`, `SIP_ECHO_TEST_EXTENSION`

### 2.4 The Issabel equivalent (commissioning, not verified on Issabel)

1. **PJSIP (Asterisk ≥ 13, tested path):**
   - Use the same two `sorcery.conf` lines, with `extconfig.conf` `ps_auths => odbc,cawp,pbx_ps_auths`. Issabel already runs `res_odbc` / unixODBC for the CDR; add the PostgreSQL ODBC driver (`postgresql-odbc` on CentOS 7) and a `res_odbc.conf` DSN to the application database, with a dedicated read-only role (`cawp_pbx_realtime`, `SELECT` on `pbx_ps_auths` only) over TLS from the PBX IP only (`pg_hba`).
   - `res_config_pgsql` is an alternative where the Asterisk build has it.
   - Put a locked fallback `auth` per WebRTC extension in `pjsip.auth_custom_post.conf`.
   - FreePBX generates `pjsip.auth.conf` from its own database, so the WebRTC test extensions must **not** have a FreePBX-managed secret, or sorcery must list only the realtime and custom sources for them. **This needs checking on the box.**
2. **chan_sip:** realtime peers (`sippeers => odbc,…` with the `md5secret` column) can serve the same HA1. chan_sip caches realtime peers (`rtcachefriends`), so set `rtcachefriends=no` for these peers, otherwise expiry and rotation are not seen until the cache drops the peer. Plan the PJSIP move.
3. **Fallback (degraded, only if the realtime link is refused):**
   - Keep a long-lived random secret per WebRTC extension in the vault and in the FreePBX extension.
   - The API holds it server-side (for example a `SIP_SECRET_<ext>` secret store) and returns it just in time over TLS with the same response shape.
   - `expires_at` then becomes a client refresh hint only, and the secret must be rotated by hand when someone leaves. Record this in the commissioning sheet if chosen.
4. **Host name:** Asterisk puts its host name into the `Contact` of WSS requests. JsSIP **rejects** a host label that starts with a digit (the Docker container id did, section 3). On Issabel, check that `hostname` is a valid DNS name, or set `external_signaling_address` / `local_net` on the WSS transport.
5. **Runbook:** a new §4d in `docs/issabel-commissioning.md` should carry items 1–4, plus a gate: "a credential superseded or older than the TTL is refused (401)". That file is not mine, so the lead should add it (open item 1).

## 3. Media and network approach (Docker Desktop for macOS)

The browser runs **inside the compose network**. Nothing about media is published.

- **`softphone-e2e-browser`:**
  - Image: `mcr.microsoft.com/playwright:v1.63.0-noble`, pinned by digest, the same version as `tools/node_modules/playwright-core`.
  - It runs `pbx-webrtc-test/e2e/browser-server.cjs`, which is `chromium.launchServer` on :3000 (published on `127.0.0.1:3310`) with the flags `--use-fake-device-for-media-stream`, `--use-fake-ui-for-media-stream`, `--ignore-certificate-errors` (self-signed test certificate only), `--disable-features=WebRtcHideLocalIpsWithMdns` (Asterisk cannot resolve `.local` names) and `--autoplay-policy=no-user-gesture-required`.
  - Two loopback forwarders make the app run on the **secure origin `http://localhost:5178`**, which `getUserMedia` and Web Locks require:
    - `localhost:5178` → `host.docker.internal:5178`, a second Vite dev server of this checkout. The verify script starts it with `VITE_API_URL=http://localhost:9796`.
    - `localhost:9796` → `softphone-e2e-api:9796`.
  - Finding: the existing dev server on :5177 listens on `::1` only, which `host.docker.internal` cannot reach. Hence the second server on 127.0.0.1:5178. The :5177 server was not touched.
- **Signalling** goes to `wss://pbx-webrtc-test:8089/ws` by service name. **Media** uses ICE host candidates between the two containers, for example `host 172.22.0.9:40770 → host 172.22.0.8:10058` (PCMU, DTLS-SRTP).
  - There is no STUN/TURN, no published RTP range and no `external_media_address`.
  - Publishing UDP ranges through Docker Desktop's userland proxy with NAT'd candidates is the unreliable path this avoids.
- **`softphone-e2e-api`:**
  - It runs the real `app.py` plus `api.calling_sip`, registered by `pbx-webrtc-test/e2e/softphone_e2e_wsgi.py` until the lead adds it to `MODULES`. The wrapper refuses any database that is not `cawp_test*`.
  - Its database, `cawp_test_call3_e2e` on `test-db`, is built by `setup_db.py`: baseline, every migration, the shared test seeds, and synthetic users `e2e.agent` (7001), `e2e.agent2` (7002), `e2e.noext` and `e2e.nocall`, all with password `Aidi@2026`.
  - It uses its own JWT secret and Redis DB 7. **`cawp-db` is never used.**
- **`pbx-webrtc-test`:**
  - It is `pbx-test` adapted for browser media: the same pinned Alpine 3.22 / Asterisk 20.11.1, plus `asterisk-pgsql`. It has WebRTC 7001–7005, a UDP transport only for the backend's digest probe, and the agent context (`_7XXX`, echo 600).
  - Test context `webrtc-test-answered` (`echo`, `tone`) stands in for the connected customer.
  - AMI has the production privileges and is published on `127.0.0.1:15038`. `hostname: pbx-webrtc-test` is required (section 2.4 item 4).
  - Limit 256 MB. It never touches `pbx-test` or `calling`.
- **Memory:** about 1.2 GB at peak (Chromium about 700 MB). The script stops all three containers at the end (`KEEP=1` keeps them).

## 4. The attempt tag and auto-answer

- **Header:** `X-CAWP-Attempt: <attempt uuid>` on the INVITE the PBX sends to the agent's extension. It is set **by the Originate itself**: `Variable: PJSIP_HEADER(add,X-CAWP-Attempt)=<id>`. For chan_sip it is `SIPADDHEADER51=X-CAWP-Attempt: <id>`. Originate variables are set on the new channel before it is called, so the header is in the first INVITE (proven live).
- `services/calling/sip_tag.attempt_tag_fields(attempt_id, driver)` returns exactly those fields. A test encodes them through P0's AMI codec together with `extension_first_fields`.
- **Auto-answer rule** (`services/softphone/policy.ts`): the softphone answers automatically **only** when all of these hold:
  - the INVITE carries the header;
  - the user's preference `auto_answer_attempts` is on;
  - `GET /v2/me/calls/active` returns an attempt with **that id, on my extension, in a live state**;
  - no other call is in conversation.

  Otherwise the call rings on the ringer device with Answer/Reject, and the reason is shown: `no_tag`, `disabled_by_preference`, `not_my_active_attempt`, `attempt_not_live`, `check_failed` or `busy_on_another_line`.
- **Why a forged header gains nothing:** agent-to-agent calls in `cawp-agents` do not copy inbound headers, and a forged header would still need the victim's current attempt id.
- **Integration, open item 2:** P1's dispatcher and P2's campaign Originate must append `attempt_tag_fields(...)`. Until they do, click-to-call legs **ring** in the softphone instead of auto-answering. That is safe, but it is not the target behaviour. I did not edit `originate.py` (P1's file).

## 5. Softphone features vs PROJECT-NOTES §3.2

| §3.2 feature | Implemented | Evidence |
|---|---|---|
| WSS register/unregister, status | JsSIP UA wrapped in `engine.ts`; header chip registered / connecting / offline / failed (plus no extension / not configured / unsupported / off) | e2e register, reconnect |
| Diagnostics panel | HTTPS, WebRTC, microphone permission, WebSocket (+URL), registration (+SIP code/cause), credential expiry countdown and issues, ICE state + selected candidate, codecs (supported + negotiated), which tab holds the phone (Web Locks / election), last error; Reconnect; echo test call | e2e: all green; screenshot 10 |
| Dialpad and DTMF | Docked panel; the idle dialpad accepts only `dial_policy.internal_pattern`; an external number is refused with a pointer to click-to-call; in-call keypad sends RFC 4733 DTMF | e2e: `0501234567` refused; 600 allowed; DTMF 5/9 sent; `#` received by the PBX (the echo test ended) |
| Multi-line, hold/resume, mute, switch | Up to 4 lines; answer, resume and switch auto-hold the others | e2e multiline |
| Transfer | Blind REFER to internal extensions only (UI and engine validate) | e2e: external target impossible; REFER to 7002 **refused by the PBX (603)** because agents have `allow_transfer=no` (open item 3) |
| Incoming, answer/reject | Auto-answer only for our tagged live attempt; other calls ring | e2e: tagged answered in about 180 ms; untagged and foreign-tagged ring |
| Audio devices, meter, sounds | Microphone/speaker/ringer select (`setSinkId`), live mic switch (`replaceTrack`), mic level meter, test tone, ringer test, WebAudio ringtone on the ringer device; saved per user through `PUT /v2/me/calling/preferences` (revision-checked, conflict reload) | e2e devices (API revision 1; nothing in browser storage) |
| Browser checks | Secure context, WebRTC, microphone permission asked once at start (so auto-answer never waits on a prompt) | diagnostics |
| Presence/availability | Not in P3: it ties into P2's readiness heartbeat (open item 6) | — |
| Call log / threads | None in the browser (no local call log) | — |
| Contacts | None in the softphone; outbound goes through click-to-call on a case contact | dialpad test |
| Recording | None in the browser (the meter reads levels, stores no samples) | code |
| Keyboard shortcuts, dark mode | Alt+Shift+P panel, A answer, E end/reject, M mute, H hold (not inside text fields); `dark:` classes follow the app theme | e2e answer/hang-up by keyboard |
| Multi-tab | Web Locks `cawp-softphone` (leader registers; followers queue and mirror a sanitized snapshot on a BroadcastChannel); heartbeat election over BroadcastChannel as the fallback; the next tab takes over when the leader closes | e2e tabs + failover |
| No SIP password in storage | Password handed to JsSIP and dropped; never in local/session storage, IndexedDB, Redux or the broadcast; fetched again on reconnect | e2e storage + broadcast scan |
| Link to attempt and case; live panel | A tagged line is linked to the verified attempt; `AttemptLive` polls P1's `GET /v2/calls/<id>/live` with ETag (case link, state pill, PBX evidence text: "your answer proves only your leg") | e2e autoanswer |
| Arabic RTL and English; Aidi Enterprise | Logical properties (`end-0`, `border-s`, `ms-`), navy primary, orange only on the active tab underline and the active-line rail, 36 px controls, status pills with glyphs, tabular numbers | 11 screenshot pairs |

## 6. Tests and evidence

### 6.1 Backend (`docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call3 -e PBX_WEBRTC_TEST_DB=cawp_test_call3 api-test tests/test_calling_sip*.py`, with the PBX started as `PBX_WEBRTC_DB_NAME=cawp_test_call3 docker compose --profile pbx-webrtc-test up -d pbx-webrtc-test`)

**`test_calling_sip.py`** (20 tests):
- **Refusals:** `calls_make` required (403 on all three routes, nothing written); no extension, legacy column only, and a released extension each give 409 `no_extension` with nothing written or audited; inactive gives 403; not configured gives 503.
- **Issue:** fields, `no-store`, TTL exactly 300 s, HA1 = md5(ext:realm:pw), and the view serves it; TTL clamping; the audit has no secret; superseded ids are audited; the revoke is audited.
- **Nothing at rest:** after an issue sent with an Idempotency-Key, the test scans **every text/json/bytea column of every table** for the password and finds nothing, and no idempotency row is written. The password is never logged.
- **Supersede and view:** a new issue supersedes (old HA1 cleared); a new owner supersedes; the view hides a credential after expiry, disable, sign-out everywhere, revoke and release; expired HA1 are cleared by the next issue.
- **Other:** status never returns the secret; rate limit (429, nothing changed); 4 concurrent issues leave one current credential; STUN/TURN REST credentials are verified by HMAC; the dial policy refuses external patterns; DB guards (CL001/CL002/CHECK); attempt-tag fields go through the AMI codec.

**`test_calling_sip_live.py`** (1 test): a real UDP REGISTER with digest authentication against the PBX:
- fresh credential: 200; wrong password: 401;
- after rotation the old password gives 401 and the new one 200;
- expired: 401; revoked: 401; released: 401.

**Whole suite** with the prefix, excluding the P0/P1 live PBX files (they need `pbx-test` and `calling`, which P2 owns now): **1083 passed, 5 skipped, 19 deselected** (230 s). The skips are P2's campaign live tests (`pbx-test` down) and the host-only dialplan check.

### 6.2 Browser end to end (`node CAWP-frontend/scripts/verify-phase5-softphone.cjs`): **78/78**

Log and JSON are in `logs/screens/phase5/softphone-verify.{log,json}`.

**Register**
- The chip shows registered, and this tab is the leader.
- Exactly 1 WSS contact for 7001 in `pjsip show contacts`, and 1 credential issued.
- The DB row holds HA1 only, TTL 90 s.
- All diagnostics are green.

**Auto-answer**
- An AMI Originate to `PJSIP/7001` with `ChannelId cawp-<attempt>` and the tag, for an attempt inserted as `dispatched` and reserved for `e2e.agent`.
- Answered automatically in **180 ms**. The line is linked to the attempt, and the live panel shows "ZZ-E2E softphone case" and the API state `dispatched`. AMI `OriginateResponse: Success`.

**Two-way audio proof** (the fake microphone → Asterisk `Echo()` → back):

| Side | Measurement |
|---|---|
| Browser `getStats` over 3 s | outbound **+24,160 B**, inbound **+24,160 B**, inbound `totalAudioEnergy` **+0.585** (real audio content returning), ICE `connected`, `host 172.22.0.9:40770 → host 172.22.0.8:10058`, PCMU |
| Asterisk `pjsip show channelstats` | `7001-00000000 ulaw`: rx **232→336**, tx **232→336** packets over 2 s, 0 lost |

**Mute**
- While muted, the media-source energy is **+0** and the returning echo energy **+0**.
- After unmute both are above 0.

**Hold**
- The transceiver is **sendonly** (the re-INVITE was accepted), with inbound +0 B and energy +0.
- Resume gives sendrecv with +19,840 B each way.

**DTMF**
- `insertDTMF` / `tonechange` fired for `5`, `9`.
- `#` on the 600 echo test made **the PBX** end the call, so the PBX received the DTMF.

**Hang-up**
- The line ended and the PBX had no channel left.

**Untagged and foreign-tagged calls**
- An untagged call still rings after 4 s (`!Ringing!` on the PBX, decision `no_tag`, answer/reject offered, the chip badge set).
- Reject gives AMI `OriginateResponse: Failure`.
- A call tagged with someone else's attempt rings (`not_my_active_attempt`); **Alt+Shift+A** answers it and **Alt+Shift+E** hangs it up.

**Multi-line**
- Answering line 2 auto-holds line 1; switching back holds line 2.
- Transfer: an external target cannot be entered; a REFER to 7002 is refused by the PBX (sip_603).

**Dialpad**
- `0501234567` is refused. `600` connects with two-way audio (+20,000 B each way).

**Devices**
- The fake devices are listed and the level meter moves.
- The microphone choice is saved in `calling_preferences` (API revision 1), and nothing is in browser storage.

**Second tab**
- It is a follower that mirrors "registered", with the "active in another tab" note.
- It sent **0** credential POSTs and opened **0** SIP WebSockets; the PBX still has 1 contact.
- It shows the ringing call without controls.
- The broadcast (9 KB) contains no password.

**Expiry**
- After the credential is expired in the DB, the view is empty.
- The PBX logs `Request 'REGISTER' from '"Agent One" <sip:7001@…>' … Failed to authenticate`.
- The phone re-fetched **54 s** after the old issue, i.e. forced by the refusal (the proactive refresh is at 63 s), and re-registered.
- The next credential was fetched proactively 62 s later, with no failure.

**Reconnect**
- After `docker restart` of the PBX, the phone went offline, reconnected, **fetched a fresh credential** and registered.

**Failover**
- Closing the leader tab makes the second tab take the lock, fetch its own credential and register; there is still 1 contact.

**Storage**
- None of the 7 issued passwords is in localStorage, sessionStorage or cookies; there is no IndexedDB; none is in the DB.
- 6 of 7 HA1 were cleared (only the current one keeps its HA1).

**No extension**
- `e2e.noext` sees the chip "no extension" and the panel "No extension assigned: ask a calling administrator…" (ar/en). There is no dialpad, and the API returns 409 `no_extension`.
- `e2e.nocall` (no `calls_make`) has no softphone at all.

**Screenshots** (`logs/screens/phase5/`):
- `softphone_01_registered`, `02_auto_answered`, `05_incoming_untagged`, `06_multiline`, `08_dialpad_external_refused`, `09_devices`, `10_diagnostics`, `11_second_tab_status_only`, `12_no_extension`, each `_ar` and `_en`;
- `03_muted_en`, `04_held_en`, `07_transfer_refused_en`.

## 7. Build, type check, lint

| Check | Before | After |
|---|---|---|
| `npm run build` | passes | **passes** (only the usual chunk-size warning) |
| `tsc --noEmit` | 4654 | **4654**; the new files have 0 |
| `eslint . --ext .ts,.tsx` | 823 errors / 2487 warnings | **823 / 2487**; the new files have 0 |
| Linux case-sensitive build (sources `rsync`ed onto a `Case-sensitive APFS` disk image, `vite build`) | — | **passes**. The negative control (`./LineCard` imported as `./Linecard`) fails with `Could not resolve "./Linecard"`, so the check is real. I used the Phase 4 method because a container `npm ci` + build was OOM-killed in Phase 4 on this 3.8 GB VM |

## 8. Files

**New:**
- `db/migrations/0056_sip_credentials.sql`
- `CAWP-API/api/calling_sip.py`, `CAWP-API/services/calling/{sip,sip_config,sip_tag}.py`
- `CAWP-API/tests/test_calling_sip.py`, `test_calling_sip_live.py`
- `pbx-webrtc-test/`: `Dockerfile`, `entrypoint.sh`, `conf/*` (20 files, 3 templates), `e2e/{setup_db.py, softphone_e2e_wsgi.py, browser-server.cjs}`
- `CAWP-frontend/src/services/softphone/{index,types,api,policy,tabs,audio,engine}.ts`
- `CAWP-frontend/src/components/softphone/{SoftphoneShell,SoftphonePanel,LineCard,Dialpad,DevicesTab,DiagnosticsTab}.tsx`, `labels.ts`
- `CAWP-frontend/scripts/verify-phase5-softphone.cjs`

**Changed (additive):**
- `docker-compose.yml`: services `pbx-webrtc-test`, `softphone-e2e-api`, `softphone-e2e-browser` and the volume `cawp_pbx_webrtc_test_keys`. Nothing else changed.
- `CAWP-frontend/package.json` + lockfile: `"jssip": "3.13.8"` (it brings `events` and `sdp-transform`; `debug`/`ms` move from dev to runtime).
- The five layouts (Classic, Modern, Decked, StackedSide, Simple): one import and `<SoftphoneShell />` in the header actions. The CRLF files keep CRLF.
- `src/locales/lang/{ar,en}.json`: 175 keys under `sp.*`, identical key sets, insertions only.

**Not touched:** `app.py`, `models.py`, `core/permissions.py`, `conftest.py`, `seed.sql`, P1's `originate.py`/`dispatch.py`, P2's files, `pbx-test/**`, the `calling` service, `docs/issabel-commissioning.md`, other screens, and `cawp-db` (only read: the migration state and the 0056 checksum).

## 9. Decisions (for PROJECT-NOTES)

1. **SIP credentials** are per-issue random passwords, checked by the PBX through a realtime PJSIP auth backend on the view `pbx_ps_auths`.
   - Only HA1 is stored, and it is cleared on revoke or expiry.
   - The PBX lookup enforces expiry (DB clock), release, deactivation and session revocation.
   - A new issue supersedes the extension's previous credential.
   - The fallback (a long-lived server-held secret) is documented for Issabel but not built.
2. **`POST /v2/me/sip-credentials` is not idempotent** (the idempotency store keeps bodies); a retry supersedes.
3. **The attempt tag** is `X-CAWP-Attempt`, set by the Originate (`PJSIP_HEADER(add,…)`). Auto-answer needs the tag to equal my live attempt from `GET /v2/me/calls/active`; otherwise the call rings.
4. **One registered tab** via Web Locks, with the BroadcastChannel election as the fallback; the other tabs show status only; closing the leader tab fails over.
5. **The softphone dials internal extensions only** (the server's pattern; external patterns are refused on both sides). Transfer is internal-only and currently refused by the PBX policy.
6. **Browser tests** run in the compose network (Playwright image + loopback forwarders on a secure localhost origin). There is no published RTP.

## 10. Open items

1. **Lead integration:**
   - add `"api.calling_sip"` to `api/__init__.py` `MODULES` (until then only the e2e wrapper registers it, and the live API on :9797 has no `/v2/me/sip-credentials`);
   - add 0056 to the `db/README.md` migration list;
   - set the `SIP_*` variables in `.env.docker` and `CAWP-API/DEPLOY.md` §5d;
   - create the `cawp_pbx_realtime` role and grant it where the PBX will read (the migration grants only if the role exists; cawp-db has no such role);
   - add runbook §4d from section 2.4 above.
2. **Attempt tag in the real Originate:** P1's dispatcher and P2's campaign originate must append `sip_tag.attempt_tag_fields(attempt_id, driver)`. Without it, click-to-call legs ring instead of auto-answering (safe, but not the target).
3. **Transfer vs `allow_transfer=no`:** the extension-first gate refuses to dial from an endpoint that can transfer. So a PBX-side internal transfer needs a reviewed design, for example `allow_transfer=yes` with a `TRANSFER_CONTEXT` limited to `_7XXX` and a revised gate check. Until then the softphone shows "The PBX refused the transfer".
4. **coturn** is not in `docker-compose.yml`: the API issues REST TURN credentials once `SIP_TURN_URLS`/`SIP_TURN_SECRET` are set. It is needed for remote agents; the compose service is still to add.
5. **Issabel realtime auth is unverified:** the ODBC DSN to PostgreSQL, the FreePBX-generated auth objects, chan_sip `rtcachefriends`, and the host name in `Contact`.
6. **Not built in P3:**
   - presence/readiness (ties into P2's heartbeat);
   - automatic wrap-up when a call ends (the calling workspace frontend); the softphone shows "wrap-up pending" and links to the case;
   - the full P1 live panel (a minimal one is embedded per line).
7. **Sign-out does not revoke the credential** (the token is gone by then); it expires after the TTL. Turning the phone off from the UI and calling `/revoke` is available in the API but has no button.
8. **HA1 is password-equivalent** for its realm while the credential is current (at most the TTL). The view's role has `SELECT` on the view only.
9. **Left behind** (small, test-only):
   - on `test-db`: the database `cawp_test_call3_e2e` and the role `cawp_pbx_realtime`;
   - images `cawp-pbx-webrtc-test:20.11.1`, `cawp-softphone-e2e-api:local` and the Playwright image (about 2 GB);
   - all three containers are stopped. The `pbx-webrtc-test/conf/sorcery.conf` comment was edited after the last image build (a comment only).
