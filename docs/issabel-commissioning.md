# Issabel commissioning runbook (Phase 5a)

This is the runbook for configuring the **real Issabel PBX** for Aidi Collections human calling: extension-first click-to-call, campaigns and the in-app WebRTC softphone. An operator applies it by hand. **The application never pushes configuration to the PBX**, and nothing in it contacts 10.10.0.220 until `CALLING_PBX_COMMISSIONED=1` is set at the end of this runbook.

The **tested reference** is the local test PBX in `pbx-test/` (Asterisk 20.11.1 LTS, `docker compose --profile pbx-test`). Every setting below was loaded and exercised there. The tests are in `CAWP-API/tests/test_calling_live_pbx.py`, and the design is in `review/phase5-foundation.md`. Section 15 lists exactly where Issabel differs from the test PBX.

Ground rules while commissioning:

- Use **dedicated test extensions** and **authorized test telephone numbers** only. Never use a customer's number.
- Keep every change in the Issabel `*_custom.conf` files (FreePBX-style). Never edit the generated files (`extensions_additional.conf`, `sip_additional.conf`, `pjsip.*.conf`, `manager.conf` banner files).
- Keep a copy of every file you change, and write the answers to the questions below into the commissioning sheet (section 16).
- Secrets (the AMI secret, the AGI key, SIP passwords) come from the vault. They go into files owned by `root:asterisk` with mode `0640`, and into the calling host's environment. Never put them in git, tickets or chat.

Scripts used by this runbook (they ship with the API source):

| File | Runs on | What it does |
|---|---|---|
| `CAWP-API/calling/issabel/pbx-epoch.sh` | the PBX, before every Asterisk start | writes a new PBX epoch UUID |
| `CAWP-API/calling/issabel/collections-globals.conf.example` | the PBX | the template for the AGI hook address and key |
| `CAWP-API/calling/issabel/pbx-verify.sh` | the PBX, as root | read-only checks, PASS/WARN/FAIL |
| `python -m calling.verify` | the calling host | read-only AMI checks, plus an optional "deny path" test call to a test extension |
| `python -m services.calling.dialplan < policy.json` | anywhere (offline) | generates the restricted dialplan |

---

## 1. Find the Issabel/Asterisk version and the channel driver

On the PBX:

```sh
cat /etc/redhat-release; rpm -qa | grep -i -E '^issabel|^asterisk' | sort
asterisk -rx "core show version"
asterisk -rx "module show like chan_"            # chan_pjsip.so and/or chan_sip.so
asterisk -rx "module show like res_http_websocket"
asterisk -rx "module show like res_srtp"
asterisk -rx "module show like codec_opus"       # optional
asterisk -rx "pjsip show endpoints" | head       # which extensions are PJSIP
asterisk -rx "sip show peers" | head             # which are chan_sip
head -3 /etc/asterisk/http.conf /etc/asterisk/manager.conf   # "generated, do not edit" banners?
grep -n "globals_custom\|extensions_custom" /etc/asterisk/extensions*.conf
```

Decisions that follow from the answers:

| Finding | Consequence |
|---|---|
| Asterisk ≥ 16 with chan_pjsip | Use the PJSIP settings (section 4a) with `webrtc=yes`, and generate the dialplan with `"driver": "PJSIP"`. This is the tested path. |
| Asterisk 13–15 with chan_pjsip | Use the PJSIP settings with the explicit options in place of `webrtc=yes` (section 4a). `func_pjsip_endpoint` must be loaded. |
| chan_sip only | Use the chan_sip variant (section 4b) and generate with `"driver": "SIP"`. The generated dialplan cannot check `allow_transfer` with chan_sip, so section 4b's `allowtransfer=no` must be verified by hand. chan_sip is removed in Asterisk 21: plan the PJSIP move. |
| `res_http_websocket` or `res_srtp` missing | WebRTC is impossible until the Asterisk package provides them. Stop and escalate. |
| Asterisk 11 (AMI banner `Asterisk Call Manager/1.3`), chan_sip | **Supported in legacy event mode** for click-to-call and campaigns (2026-10-21; the Alitqan Issabel 4 runs 11.25.3). Set `CALLING_DRIVER=SIP` **and** `CALLING_PBX_EVENTS=legacy` on the API and the calling service, and generate the dialplan with `"driver": "SIP"`, `"pbx_events": "legacy"` (section 6). Why: Asterisk 11 ignores the Originate `ChannelId` and its events have no `Linkedid` and use `Dial` Begin/End and `Bridge` Link/Unlink, so the agent leg is bound by the Originate's ActionID (`OriginateResponse`) or the FastAGI hook, and the events are normalised (`services/calling/legacy_events.py`, `legacy_correlation.py`). The dispatcher refuses every call with `pbx_version_mismatch` when the AMI greeting contradicts `CALLING_PBX_EVENTS` (AMI 1.x = Asterisk ≤ 11). The browser softphone (WebRTC) is not part of this (desk phones). |
| Asterisk 12 | Not supported (neither event model was tested on it). |
| Asterisk < 11 | Not supported. |

## 2. Test extensions

In the Issabel web UI (PBX → Extensions), create a few test extensions for commissioning, for example 7001–7005, each with a strong random secret from the vault. Assign them to test users in the app (section 12). Section 4 makes them WebRTC-capable. Keep one plain SIP desk phone (or a softphone such as Linphone) as a non-WebRTC control.

## 3. Dedicated AMI account

Add to `/etc/asterisk/manager_custom.conf` (included at the end of the generated `manager.conf`):

```ini
; --- CAWP calling service (docs/issabel-commissioning.md §3) ---
[general](+)
timestampevents = yes            ; PBX time on every event; evidence ordering never uses receipt time
tlsenable = yes                  ; AMI over TLS on 5039 (or use the SSH-tunnel option below)
tlsbindaddr = 0.0.0.0:5039
tlscertfile = /etc/asterisk/keys/pbx.crt       ; full chain, CN/SAN = the name the calling host uses
tlsprivatekey = /etc/asterisk/keys/pbx.key

[cawp-calling]
secret = <from the vault>
deny = 0.0.0.0/0.0.0.0
permit = <calling host IP>/255.255.255.255
read = call,user,system          ; channel/dial/bridge events, UserEvent CollectionsCall, FullyBooted/Shutdown
write = originate,reporting      ; Originate to a context/exten; Getvar/CoreStatus/CoreShowChannels/ShowDialPlan
writetimeout = 5000
```

Then run `asterisk -rx "manager reload"`, followed by `asterisk -rx "manager show user cawp-calling"` and `asterisk -rx "manager show settings"`.

Why these privileges, and what they deny (all checked on the test PBX):

- `originate` allows Originate into a context and extension only. An *application* Originate (for example `System`) additionally needs `system`, so it is refused ("Originate Access Forbidden").
- `reporting` gives read-only actions: Getvar of the epoch global, CoreStatus, CoreShowChannels and ShowDialPlan.
- There is **no** `system`, `command`, `config` or `call` write, so this account cannot use CLI `Command`, `Reload`/`ModuleLoad`, `Setvar` (which could overwrite the AGI URL global), `Redirect`, transfers or `Bridge`.
- Hanging up a call from the application (`Hangup`) would need `call` write. That also grants `Setvar`, so it is deliberately left out. The dialplan's absolute timeout enforces the maximum duration on the PBX (section 6). If P1 decides it needs a server-side hang-up, that is a separate reviewed change.
- **2026-10-20, call control (section 3b):** transfers, conferences and supervisor listening need `call` write (`Redirect`, `Setvar`, `PlayDTMF`, `Hangup`). The account becomes `write = originate,reporting,call`. `call` would also allow a *global* `Setvar` (for example of `COLLECTIONS_AGI_URL`) or a `Redirect` into any context, so the client (`services/calling/ami.py`, `call_control_fields_ok`) refuses those whatever the caller passes: `Setvar` only on a named channel and only `CAWP_CTL_*`/`CAWP_SPY_*` variables with plain values, `Redirect` only into `cawp-callctl-*` contexts, `PlayDTMF` only the digits 4/5/6 with `Receive: yes`. The ACL (one host) and TLS stay. `system`, `command`, `config` remain refused (`pbx-verify.sh`).

## 3b. Call control: transfer, conference, supervisor listening (2026-10-20)

Server-controlled, never SIP REFER: every endpoint keeps `allow_transfer = no` (the click-to-call gate needs it). The `calling` service sends AMI actions into its own contexts; nothing else reaches them.

Files (generated, review before installing):

```sh
cd CAWP-API
echo '{"post_call_context":"cawp-survey","record":true,"max_call_seconds":3600,"ring_seconds":30,"recall_seconds":20}' \
  | python -m services.calling.callctl_dialplan > cawp-callctl.conf          # -> /etc/asterisk/cawp-callctl.conf
python -m services.calling.callctl_dialplan --confbridge > confbridge_custom.conf  # -> /etc/asterisk/confbridge_custom.conf
```

Install on Issabel (back up first; check `core show channels` shows no customer call before reloading):

```sh
B=/root/cawp-backup-callctl-$(date +%Y%m%d-%H%M%S); mkdir -p $B
cp -a /etc/asterisk/manager_custom.conf /etc/asterisk/extensions_custom.conf /etc/asterisk/confbridge_custom.conf $B/
install -o root -g asterisk -m 0640 cawp-callctl.conf /etc/asterisk/cawp-callctl.conf
cat confbridge_custom.conf > /etc/asterisk/confbridge_custom.conf      # confbridge.conf already #includes it
printf '\n; CAWP call control (services/calling/callctl_dialplan.py)\n#include cawp-callctl.conf\n' >> /etc/asterisk/extensions_custom.conf
sed -i '/^\[cawp-calling\]/,/^\[/{s/^write = originate,reporting$/write = originate,reporting,call/}' /etc/asterisk/manager_custom.conf
asterisk -rx "dialplan reload"; asterisk -rx "module reload app_confbridge.so"; asterisk -rx "manager reload"
```

Check: `dialplan show cawp-callctl-room`, `confbridge show profile bridge cawp_room`, `confbridge show profile user cawp_customer`, `manager show user cawp-calling` (write perm includes call), `module show like app_chanspy` / `app_confbridge`. The calling service must reconnect (restart it) to log in with the new permissions. Rollback: copy the backup files back and run the same three reloads.

What happens on the PBX (Asterisk 18):

| Action | AMI from the calling service | Dialplan |
|---|---|---|
| Blind transfer | `Setvar` CAWP_CTL_ID/FROM on the peer, CAWP_CTL_ID on the agent; one dual `Redirect` (peer → `cawp-callctl-xfer,<ext>`, agent → `cawp-callctl-done`) | the peer dials the target with the `X-CAWP-Control` header (pre-dial handler); no answer → rings the transferring agent back once; MixMonitor `<date>/<uniqueid>-cc.wav` on the customer leg |
| Consult first | `Setvar` room/holder; dual `Redirect` (peer → `cawp-callctl-hold`, agent → room); `Originate` the target into the room | hold music; if the agent's channel disappears and the target is in the room, the customer joins it, else post-call |
| Complete / cancel | `Redirect` peer → room + agent → done / `Hangup` target + `Redirect` peer → room | |
| Add participant | `Setvar` room on both; dual `Redirect` into the ConfBridge room; `Originate` the invitee | profiles `cawp_room`, `cawp_customer` (marked), `cawp_member` (end_marked) |
| Listen / whisper / join | `Originate` the supervisor's own extension into `cawp-callctl-spy,<mode>` | `ChanSpy(<agent channel>,qdEu[w|B])`; `PlayDTMF Receive` 4/5/6 switches the mode |

The click-to-call agent leg is never masqueraded (Redirect keeps the channel and its uniqueid), so the attempt's evidence stays attributed; the agent's own MixMonitor keeps recording while the agent is in a room, and the customer leg gets its own recording once it leaves the agent. `CALLING_CONTROL_ENABLED=0` on the calling service turns the worker off.

**Option without AMI TLS:** keep AMI bound to `127.0.0.1:5038` and run an SSH tunnel from the calling host (`ssh -N -L 127.0.0.1:5038:127.0.0.1:5038 pbx`). Then set `CALLING_AMI_TLS=0` with `CALLING_AMI_HOST=127.0.0.1` on the calling host. The client accepts plaintext only to loopback. Never expose 5038 on the network.

## 4. HTTPS/WSS and WebRTC endpoints

### 4.0 http.conf (TLS for the WebSocket)

Browsers only connect to `wss://` with a certificate they trust. Use a certificate from the company CA (installed on the agent PCs) or a public certificate, for the exact name agents use (for example `pbx.aidi.internal`). A self-signed certificate is test-only.

If `http.conf` is generated, put these in `http_custom.conf` (check the include in `http.conf`); otherwise add them to `http.conf`:

```ini
[general]
enabled = yes
bindaddr = 127.0.0.1          ; plain HTTP only locally (the FreePBX/Issabel UI does not use it)
bindport = 8088
tlsenable = yes
tlsbindaddr = 0.0.0.0:8089
tlscertfile = /etc/asterisk/keys/pbx.crt
tlsprivatekey = /etc/asterisk/keys/pbx.key
enablestatic = no
```

Apply it with `module reload http` and check it with `http show status`: "HTTPS Server Enabled and Bound to 0.0.0.0:8089" and `/ws` must appear. The WebSocket URL for the softphone (P3) is `wss://<pbx name>:8089/ws`.

### 4a. PJSIP (tested)

Put these in `pjsip.transports_custom.conf` and `pjsip.endpoint_custom_post.conf` (or use the Issabel extension form's advanced fields where they exist):

```ini
; transport (once)
[transport-wss]
type = transport
protocol = wss
bind = 0.0.0.0

; per WebRTC agent extension (repeat for 7001..)
[7001](+)
transport = transport-wss
webrtc = yes                 ; Asterisk >= 16: = use_avpf, media_encryption=dtls, dtls_verify=fingerprint,
                             ;   dtls_setup=actpass, ice_support, media_use_received_transport, rtcp_mux
dtls_auto_generate_cert = yes
allow_transfer = no          ; REQUIRED: the dialplan refuses to dial when this is not "false"
direct_media = no
disallow = all
allow = ulaw,alaw            ; add opus only if codec_opus is installed (else transcoding to the trunk fails)
max_audio_streams = 1
device_state_busy_at = 1
; Asterisk 13-15 (no webrtc=): use_avpf=yes, media_encryption=dtls, dtls_verify=fingerprint,
;   dtls_setup=actpass, ice_support=yes, media_use_received_transport=yes, rtcp_mux=yes,
;   dtls_cert_file=/etc/asterisk/keys/pbx.crt, dtls_private_key=/etc/asterisk/keys/pbx.key
[7001](+)                    ; the AOR
type = aor
max_contacts = 1
remove_existing = yes
```

Set `allow_transfer = no` on the **outbound trunk endpoint** as well. Apply with `module reload res_pjsip.so` and check with `pjsip show endpoint 7001`.

### 4b. chan_sip variant

Put these in `sip_general_custom.conf` and `sip_custom_post.conf`:

```ini
; sip_general_custom.conf
transport = udp,ws,wss
websocket_enabled = yes

; sip_custom_post.conf, per WebRTC agent extension
[7001](+)
transport = wss
avpf = yes
encryption = yes
force_avp = yes
icesupport = yes
dtlsenable = yes
dtlsverify = fingerprint
dtlscertfile = /etc/asterisk/keys/pbx.pem     ; cert + key in one file for chan_sip
dtlssetup = actpass
rtcp_mux = yes                                 ; Asterisk >= 13.15
directmedia = no
nat = force_rport,comedia
allowtransfer = no
disallow = all
allow = ulaw,alaw
```

Apply with `sip reload` and check with `sip show peer 7001`. With chan_sip, generate the dialplan with `"driver": "SIP"`.

### 4c. Codecs

Browsers support PCMU/PCMA (ulaw/alaw) natively; Opus is better on poor networks. Asterisk needs the `codec_opus` module to *transcode* Opus to the trunk's codec, and it is not part of plain Asterisk builds. Use `allow = ulaw,alaw` unless `codec_opus` is installed and tested. The trunk codec (usually alaw or ulaw) must be in the same list, so no transcoding is needed.

## 5. The PBX epoch (pre-start hook)

The calling service identifies the **Asterisk process** it talks to by the global `COLLECTIONS_HUMAN_PBX_EPOCH` (a new UUID on every start) combined with the process start time from AMI CoreStatus. When either changes, in-flight calls from the previous process become `uncertain`, and evidence from the two processes is never mixed.

```sh
install -o root -g root -m 0755 CAWP-API/calling/issabel/pbx-epoch.sh /usr/local/sbin/cawp-pbx-epoch.sh
/usr/local/sbin/cawp-pbx-epoch.sh /etc/asterisk/collections-pbx-epoch.conf     # first run
```

Run the hook before **every** Asterisk start (not on reloads):

- **systemd** (`systemctl status asterisk` shows a unit): `systemctl edit asterisk`, then add
  ```ini
  [Service]
  ExecStartPre=/usr/local/sbin/cawp-pbx-epoch.sh /etc/asterisk/collections-pbx-epoch.conf
  ```
- **init script / `amportal` / `fwconsole start`** (Issabel 4 on CentOS 7 often uses these): call the hook at the top of the start function, before `safe_asterisk` is launched.

If `safe_asterisk` restarts Asterisk after a crash, the hook does not run. The process start time still changes, so the combined epoch still changes (the calling service reports `pbx_restarted`). `pbx-verify.sh` and `python -m calling.verify` both show the epoch.

Include the file inside `[globals]`. On Issabel/FreePBX, `[globals]` in `extensions_additional.conf` ends with `#include globals_custom.conf`, so add this line to `/etc/asterisk/globals_custom.conf`:

```ini
#include collections-pbx-epoch.conf
```

If `grep -n globals_custom /etc/asterisk/extensions_additional.conf` finds nothing, put a `[globals](+)` section with the include in `extensions_custom.conf` instead.

To verify: `asterisk -rx "dialplan show globals" | grep COLLECTIONS_HUMAN_PBX_EPOCH`. After `dialplan reload` the value must be **unchanged**; after a full restart it must be **new**.

## 6. The restricted dialplan

On the calling host, write the policy and generate the dialplan (nothing is installed by this):

```json
{
  "context": "collections-human-outbound",
  "driver": "PJSIP",
  "trunk": "<outbound trunk endpoint name>",
  "max_call_seconds": 600,
  "ring_seconds": 30,
  "record": true,
  "calling_windows": [{"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "20:00"}]
}
```

```sh
docker exec -i cawp-api python -m services.calling.dialplan < policy.json > collections-human.conf
```

Copy the output to `/etc/asterisk/collections-human.conf` (owner `root:asterisk`, mode `0640`), add `#include collections-human.conf` to `extensions_custom.conf`, then run `asterisk -rx "dialplan reload"`. Check with `dialplan show collections-human-outbound`.

What the generated context does (the same text runs on the test PBX; see `pbx-test/conf/extensions_collections.conf`):

1. It runs on the **agent** channel only after the agent answered the extension-first Originate. It clears the gate variables and pushes a hangup handler.
2. It calls `AGI(${GLOBAL(COLLECTIONS_AGI_URL)})`. `GLOBAL()` makes sure that a channel variable cannot redirect the hook. The calling service's FastAGI server consumes a **one-shot dispatch token** bound to this channel (`cawp-<attempt id>`), the destination, the extension, the context and the PBX epoch. It then sets the gate outputs and `COLLECTIONS_GATE=ALLOW` last.
3. It re-checks every gate output: the E.164 destination equal to the dialled extension, the extension, the limit (30..max), the expiry, the calling windows (Asia/Riyadh `GotoIfTime`), the expiry against `${EPOCH}`, and `allow_transfer=false` on the agent endpoint and the trunk. Any failure leads to `Hangup(21)`, never to Dial.
4. It sets `TIMEOUT(absolute)` to the granted limit, `CDR(userfield)=cawp:<attempt>` and `CDR(recordingfile)`, and starts `MixMonitor` (bridged audio only).
5. It dials `PJSIP/<E.164>@<trunk>` with ring timeout, `i` (ignore forwarding), a pre-dial handler (`b`, customer events), `U` (customer-answered event) and `L` (duration limit). There are no transfer or continuation options.
6. It emits `UserEvent(CollectionsCall, AttemptId, State)` at each step: agent_answered, authorized, customer_dialing, customer_created, customer_answered, dial_end, denied, customer_ended, agent_ended (with DIALSTATUS).

Rules:

- **Never include `collections-human-outbound` in any other context**, and never give it to an endpoint as its `context`. Only AMI Originate enters it. `pbx-verify.sh` checks for includes.
- The dialplan dials the trunk **directly**, not through FreePBX outbound routes. Outbound-route caller-ID and prefix rules do not apply: set the caller ID on the trunk. If the carrier needs national format (`05…`) instead of `+9665…`, use `"dial_format": "national_sa"` (below). The live test with an authorized test number (section 13) proves the format.

Optional policy keys (absent = the output above, byte for byte):

| Key | Values | Effect |
|---|---|---|
| `dial_format` | `e164` (default) \| `national_sa` | `national_sa`: the trunk is dialled with `0` + the 9 digits after `+966` (`+9665XXXXXXXX` → `05XXXXXXXX`), and any destination that is not `+966` + 9 digits is **denied** (`Hangup(21)`). The Originate `Exten`, the AGI-granted destination and the dispatch token stay E.164. |
| `trunks` | `["Mobily18", "Mobily19", …]`, 1–16 distinct names, the first equal to `trunk`; **driver `SIP` only** (more than one name with `PJSIP` is refused) | **Trunk selection, one Dial**: before dialling, the dialplan takes the FIRST trunk in that order whose chan_sip `${SIPPEER(<trunk>,status)}` starts with `OK` (the peer answers `qualify`) and dials only that one (`SIP/${COLLECTIONS_TRUNK}/<number>`). Whatever the `DIALSTATUS`, no other trunk is tried afterwards: chan_sip maps far-end answers such as 480, 404 or 503 to `CONGESTION` / `CHANUNAVAIL`, so retrying on those statuses could ring the same customer on several gateways. No trunk `OK` → `UserEvent` `no_trunk`, `Hangup(34)`, no Dial (the attempt fails `no_trunk_available`). Every trunk in the list needs `qualify=yes` (an unmonitored peer reports `Unmonitored` and is never selected). The projection accepts exactly one customer leg per attempt; a second one is `multiple_customer_legs` (uncertain). |
| `pbx_events` | `modern` (default) \| `legacy` | `legacy` (Asterisk ≤ 11): every `CollectionsCall` UserEvent body also carries `ChanUniqueid: ${UNIQUEID}` (the agent channel, or the customer channel in the pre-dial, answer and hangup handlers). Must match `CALLING_PBX_EVENTS`. |

The Alitqan Issabel (Asterisk 11.25, chan_sip, GSM gateways `Mobily18`…`Mobily26` as chan_sip trunks in the catch-all route `Direct_outside`, agents dialling `05XXXXXXXX`):

```json
{
  "context": "collections-human-outbound",
  "driver": "SIP",
  "trunk": "Mobily18",
  "trunks": ["Mobily18", "Mobily19", "Mobily20", "Mobily21", "Mobily22", "Mobily23", "Mobily24", "Mobily25", "Mobily26"],
  "dial_format": "national_sa",
  "pbx_events": "legacy",
  "max_call_seconds": 600,
  "ring_seconds": 30,
  "record": true,
  "calling_windows": [{"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "20:00"}]
}
```

Take the trunk list and its order from `Direct_outside` (Issabel → Outbound Routes) at commissioning time; the dialplan dials the first reachable one of those trunks directly with the customer leg string `SIP/<trunk>/05XXXXXXXX`. Every construct the generator emits exists in Asterisk 11 (`CHANNEL(hangup_handler_push)` and the Dial `b()` pre-dial handler are Asterisk 11 features; `U(context)` is a Gosub to `context,s,1`; `GotoIfTime` takes the timezone). Check `sip show peers` first: every listed gateway port must show `OK (… ms)` when it can carry calls (`qualify=yes`). Verify with `python -m calling.verify --driver SIP --pbx-events legacy --trunk Mobily18,Mobily19,…` (the selection order and exactly one Dial of the selected trunk).
- The **calling windows** in the policy must match the app's policy (`PUT /v2/calling/windows`). The app enforces windows and holidays at dispatch; the PBX check is the second line of defence. Holidays are app-only, because `GotoIfTime` has no year. Regenerate and reload whenever the approved hours change.

## 7. Globals for the AGI hook

```sh
install -o root -g asterisk -m 0640 CAWP-API/calling/issabel/collections-globals.conf.example \
        /etc/asterisk/collections-globals.conf
# edit: COLLECTIONS_AGI_URL=agi://<calling host as the PBX reaches it>:4573/collections-authorize
#       COLLECTIONS_AGI_KEY=<the same value as CALLING_AGI_SECRET on the calling host>
echo '#include collections-globals.conf' >> /etc/asterisk/globals_custom.conf
asterisk -rx "dialplan reload"
```

The FastAGI server answers ALLOW only when the connection comes from an allowed peer (`CALLING_AGI_ALLOWED_PEERS` = the PBX IP), the channel's `${GLOBAL(COLLECTIONS_AGI_KEY)}` matches, and the token checks pass. Every decision, allow or deny, is stored in `call_agi_decisions` with a fixed reason. If the calling service is down, `AGISTATUS` is not SUCCESS and the customer is never dialled.

## 8. Recording

The generated dialplan records bridged audio with `MixMonitor(${COLLECTIONS_RECORDING},b)`, where the file name is `YYYY/MM/DD/<uniqueid>.wav` (Asia/Riyadh date, `uniqueid` = `cawp-<attempt id>`). The path is relative to the Asterisk monitor directory, `/var/spool/asterisk/monitor/`. This is the same layout Issabel uses for its own recordings, and it matches the API's `RECORDINGS_ROOT=/var/spool/asterisk/monitor/`, so the existing `/cdr/recording` route can serve it. `CDR(recordingfile)` holds the relative path, as the legacy screens expect.

Check that `/var/spool/asterisk/monitor` is owned by `asterisk` and has enough free space (`pbx-verify.sh` warns below 10 GiB). Retention of recordings is an open policy item (PROJECT-NOTES §2.10).

## 9. CDR

Issabel writes CDRs to MySQL `asteriskcdrdb.cdr` (cdr_mysql or cdr_adaptive_odbc), which the API already reads through `issable/db_connection.py`. For calls placed by the application:

- `uniqueid` = `cawp-<attempt id>` (the agent leg);
- `userfield` = `cawp:<attempt id>`;
- `recordingfile` = the MixMonitor path;
- `dst` = the E.164 number and `dcontext` = `collections-human-outbound`.

**`userfield` MUST be logged (required, P1).** On Issabel `asteriskcdrdb.cdr.uniqueid` is `varchar(32)`, so `cawp-<attempt id>` (41 characters) is stored **truncated** (`cawp-` plus the first 27 characters of the id). The `userfield` tag `cawp:<attempt id>` is therefore the only reliable link from a CDR row to its call attempt: the reconciliation CDR lookup (`services/calling/cdr_lookup.py`) and the P4 attribution (`services/calling_cdr.py`) match `userfield` first, and accept a truncated `uniqueid` only when it names exactly one attempt. Every lookup is bounded by a date window around the attempt (`calldate` from 1 h before the attempt to 12 h after), so an index on `calldate` (Issabel has one) keeps it cheap.

**Asterisk 11 (legacy mode).** `asteriskcdrdb.cdr` has **no `linkedid` column** and `uniqueid` is the agent channel's `"<epoch>.<seq>"` (Asterisk 11 ignores the Originate ChannelId), so `userfield = cawp:<attempt id>` is the **only** link from a CDR row to its attempt (`cdr_mysql` with `userfield=1`). The record of an originated call is the AGENT channel's (`channel` `SIP/<ext>-…`, `dstchannel` the trunk leg): it is `ANSWERED` as soon as the agent answered and its `billsec` counts from the agent's answer. It therefore never proves that the customer answered, and its `billsec` is never used as the conversation duration: the reconciliation lookup reports `billsec: null` (`billsec_source: agent_leg`, the raw value as `agent_billsec`); the duration comes from the Bridge Link → Unlink of the agent and customer legs. An `ANSWERED` record with a dial only keeps an uncertain attempt for human review (`conversation_unproven`).

Check with `cdr show status`. After the test call, run:
`SELECT calldate, uniqueid, userfield, recordingfile, disposition, billsec FROM asteriskcdrdb.cdr WHERE calldate > now() - INTERVAL 1 HOUR AND userfield LIKE 'cawp:%' ORDER BY calldate DESC LIMIT 5;`
Every row of the test call must show `userfield=cawp:<attempt id>` (the full 36-character id). If `userfield` stays empty, enable user-field logging in the CDR backend before going further: for `cdr_mysql`, `userfield=1` in `cdr_mysql.conf` `[global]`; for `cdr_adaptive_odbc`, map the `userfield` column (`alias userfield => userfield`) in `cdr_adaptive_odbc.conf`; then `module reload cdr_mysql.so` (or the ODBC module). The test PBX writes the same records to CSV (`Master.csv`, `loguserfield = yes`) instead of MySQL.

For reconciliation the calling host reads the CDR with a read-only MySQL account: set `CALLING_CDR_SOURCE=mysql` and the existing `CDR_HOST`, `CDR_DB`, `CDR_USER`, `CDR_PASSWORD` on the calling host (the service refuses `10.10.0.220` until `CALLING_PBX_COMMISSIONED=1`). The account needs `SELECT` on `asteriskcdrdb.cdr` only.

## 10. TURN/STUN (coturn)

Agents on the office LAN usually reach the PBX directly (ICE host candidates). Remote agents or strict NATs need TURN. Run coturn as a container next to the application; the compose service is a Phase 5 P3 item. The API will issue short-lived TURN credentials (the REST shared-secret scheme), so no long-lived TURN password reaches a browser. `turnserver.conf`:

```ini
listening-port=3478
tls-listening-port=5349
realm=aidi-collections
use-auth-secret
static-auth-secret=<from the vault; also given to the API>
cert=/etc/coturn/tls.crt
pkey=/etc/coturn/tls.key
external-ip=<public or LAN IP the browsers reach>
min-port=49160
max-port=49200
fingerprint
no-cli
no-multicast-peers
no-tcp-relay
denied-peer-ip=0.0.0.0-0.255.255.255
denied-peer-ip=127.0.0.0-127.255.255.255
denied-peer-ip=10.0.0.0-10.255.255.255
allowed-peer-ip=<PBX IP>                 ; relay only to the PBX media ports
```

On the PBX, `rtp.conf` must have `icesupport=yes` (default). A `stunaddr=` is only needed when the PBX itself is behind NAT from the agents' point of view.

## 11. Firewall

| From | To | Port | Why |
|---|---|---|---|
| agent browsers | PBX | 8089/tcp | WSS signalling |
| agent browsers (or coturn) | PBX | RTP range in `rtp.conf` (Issabel default 10000–20000/udp) | media (DTLS-SRTP) |
| agent browsers | coturn | 3478/udp+tcp, 5349/tcp, 49160–49200/udp | TURN (remote agents) |
| calling host | PBX | 5039/tcp (or SSH 22 for the tunnel option) | AMI over TLS |
| **PBX** | calling host | 4573/tcp | FastAGI authorization hook |
| API host | PBX | 3306/tcp, 22/tcp | existing CDR read and recording fetch |
| anyone else | PBX | 5038, 5039, 8088 | **deny** |

AMI and the plain HTTP port must never be reachable from the internet or the office LAN.

## 12. Application side

1. Apply migrations 0048–0053 (`python manage_db.py up`). This also merges the permission keys `calls`, `calls_make` and `calls_manage`. 0052 adds the click-to-call dispatch queue, the calling-service heartbeat, the reconciliation log and the review flags; 0053 adds the locked/archived wrap-up review fields.
2. In the app, assign each test extension to its test user (calling admin, `POST /v2/calling/extensions/<ext>/assign`). `user_extensions` is the source of truth, and `users.extension` is kept in sync as a mirror. Use the legacy-drift list to adopt the existing legacy values one by one.
3. Save the approved calling windows (`PUT /v2/calling/windows`) with the same hours as the dialplan policy.
4. Set the calling host environment (see `CAWP-API/DEPLOY.md` §5d): `CALLING_AMI_HOST`, `CALLING_AMI_CA_FILE`, `CALLING_AMI_USERNAME`, `CALLING_AMI_SECRET`, `CALLING_AGI_SECRET`, `CALLING_AGI_ALLOWED_PEERS=<PBX IP>`. **Leave `CALLING_ENABLED=0`** until section 13 passes, apart from the explicit verification runs below.
5. Click-to-call (P1) settings. On the calling host: `CALLING_DISPATCH_ENABLED=1` (the dispatcher), `CALLING_RECONCILE_SECONDS=10`, `CALLING_CDR_SOURCE=mysql` (section 9). On the API host: `CALLING_CONTEXT` (the policy context, default `collections-human-outbound`), `CALLING_DRIVER` (`PJSIP` or `SIP`), `CALLING_AGENT_RING_SECONDS` (default 30), `CALLING_MAX_CALL_SECONDS` (default 600; it must not exceed the installed dialplan's `max_call_seconds`, or the gate denies every call) and `CALLING_DISPATCH_TTL_SECONDS` (default 30). The API refuses `POST /v2/calls` with 503 `transport_not_connected` unless the calling service reported a connected AMI session within 15 s (table `calling_service_status`).

## 13. Verification checklist

Run the scripts first. Both are read-only unless stated.

```sh
# on the PBX
sudo ./pbx-verify.sh --ami-user cawp-calling --extension 7001 --calling-host <calling host> \
     --trunk <trunk> --cert /etc/asterisk/keys/pbx.crt
# on the calling host (reads CALLING_AMI_* from the environment)
python -m calling.verify --host <pbx> --ca-file <ca.pem> --trunk <trunk> --agi-host <calling host>
# deny path: rings test extension 7001; answer it; the AGI hook must refuse; no customer leg
python -m calling.verify --host <pbx> --ca-file <ca.pem> --place-deny-test 7001 --i-am-at-the-extension
# Asterisk 11 / chan_sip (Alitqan): the same, legacy event model, trunk selected by SIPPEER status, one Dial
python -m calling.verify --host <pbx> --driver SIP --pbx-events legacy --trunk Mobily18,Mobily19,... \
       --place-deny-test <test ext> --i-am-at-the-extension
```

In legacy mode `calling.verify` also checks that the AMI greeting is 1.x, finds the agent channel from the Originate's own ActionID (no ChannelId/Linkedid), and reports a missing `Timestamp` header as WARN. Gates 3, 4, 5, 10, 13 and 16 below must be run **on the Asterisk 11 PBX itself** before `CALLING_PBX_COMMISSIONED=1`: the legacy correlation (OriginateResponse binding, `Dial` Begin customer legs, `Bridge` Link as the conversation, `ChanUniqueid` in UserEvents, the order of `OriginateResponse` vs. the FastAGI call (the hook allows only the agent leg that response bound, waiting up to 2.5 s for it), the trunk selection by `SIPPEER(<trunk>,status)`, a hold/Redirect masquerade (`leg_masqueraded`, uncertain), the national number on the GSM gateways) is proven by unit tests with frames of the captured key sets only. On Asterisk 11 the recording file and the CDR `uniqueid` are the agent channel's `"<epoch>.<seq>"`, not `cawp-<attempt>` (gates 12 and 13: match by `userfield`).

Expected: every line PASS (WARN lines explained in the sheet). On the test PBX, `pbx-verify.sh` gives 33 PASS with two expected WARNs (container disk space, and CSV instead of MySQL CDR), and `calling.verify` passes every check including the deny test.

Then work through the acceptance gates with test extensions and authorized test numbers only. Each gate needs a passing result recorded in the sheet:

| # | Check | Expected evidence |
|---|---|---|
| 1 | WebRTC registration of 7001 from a browser over WSS | `pjsip show contacts` shows the WSS contact; the softphone shows "registered" |
| 2 | Call from 7001 to a desk extension and back | two-way audio; DTLS-SRTP (`pjsip show channelstats`) |
| 3 | Extension-first call to an authorized test number (enable the calling service for this test only) | agent rings first; after the agent answers, AGI allow; customer rings; UserEvents in `call_events`; both legs in `call_legs` with bridge |
| 4 | Customer busy / no answer / rejects | `dial_end` DialStatus BUSY / NOANSWER / CONGESTION; no bridge |
| 5 | Hang up from the agent side, then from the customer side | `agent_ended` / `customer_ended` with causes; the reservation is kept until wrap-up |
| 6 | Maximum duration | with a 30-second limit, the call ends at about 30 s; `TIMEOUT(absolute)` fired |
| 7 | Replay: repeat the same token | AGI deny `token_consumed`; no second customer Dial |
| 8 | Calling service stopped during a call | the call continues on the PBX, bounded by the limit; the attempt becomes `uncertain` (`calling_service_stopped`); the reservation is kept |
| 9 | Network drop between the calling host and the PBX | the AMI session ends; the attempt is `uncertain` (`ami_connection_lost`); reconnect with the same epoch |
| 10 | PBX restart | the new epoch is visible; `pbx_restarted=true`; a call that was live when Asterisk was killed is `uncertain` (`ami_connection_lost` / `pbx_epoch_changed`) with its reservation kept, then reconciled (`call_reconciliations`: `bridged_then_lost` when both legs had been bridged, `no_customer_contact` when the gate never allowed it). A graceful `core restart` hangs up the channels first, so the call ends by evidence |
| 11 | Second browser tab (P3) | only one tab registers |
| 12 | Recording | the file exists under `monitor/YYYY/MM/DD/cawp-<attempt>.wav` and plays via `/cdr/recording` with the recording keys |
| 13 | CDR | the rows have `userfield=cawp:<attempt>` (full id; **required**), `uniqueid` = `cawp-<attempt>` truncated to 32 characters, `recordingfile` |
| 16 | Click-to-call from the app (P1) | `POST /v2/calls` -> the attempt goes authorized -> dispatched -> agent_answered -> customer_dialing -> customer_answered -> bridged -> ended in `GET /v2/calls/<id>/live`; the wrap-up releases the reservation |
| 17 | Agent does not answer | the attempt `failed` (`agent_no_answer`), no AGI decision, the reservation released as `no_customer_contact` |
| 14 | Outside the calling window | the PBX denies after `authorized`; no customer Dial |
| 15 | AGI key wrong or calling host unreachable | deny (`agi_key_mismatch`) or `AGISTATUS` FAILURE; no customer Dial |

Only after all gates pass: set `CALLING_PBX_COMMISSIONED=1` and `CALLING_ENABLED=1` in the production environment, and record the date, the operator and the Asterisk version in the sheet.

## 14. Rollback

In order of impact:

1. Set `CALLING_ENABLED=0` and restart the calling service. No AMI session and no AGI answers, so every customer Dial is denied.
2. Remove `#include collections-human.conf` from `extensions_custom.conf` and run `dialplan reload`.
3. Remove the `[cawp-calling]` AMI user and run `manager reload`.
4. Reverse the endpoint changes (`*_custom_post.conf`) and reload.

Reservations and attempts in the database stay as they are. They are never deleted. With the calling service off, queued click-to-calls are never sent (they expire), and uncertain attempts wait for reconciliation or a supervisor (`GET /v2/calling/uncertain`, `POST /v2/calls/<id>/resolve` with a reason).

## 15. What differs from the test PBX

| Item | Test PBX (`pbx-test/`) | Issabel production |
|---|---|---|
| Asterisk | 20.11.1 LTS, Alpine 3.22 (pinned by digest) | whatever section 1 finds (often 13 or 16 on Issabel 4), CentOS 7 |
| Config layout | our own flat files, rendered at start | FreePBX-generated files plus `*_custom.conf` |
| Customer network | loopback trunk `cawp-test-trunk` to 127.0.0.2; the last digit decides (1 answer, 2 busy, 3 no answer, 4 answer then hang up, 5 congestion, 6 late answer) | the real SIP trunk; authorized test numbers only |
| Agent phones | 7001–7005 WebRTC, 7101–7102 UDP (7101 = bundled pjsua, auto-answer), 7201–7204 simulated (7201 answers, 7202 busy, 7203 never answers, 7204 answers after 3 s) | real extensions and the in-app softphone |
| Restart control | TEST-ONLY port 5099 (`<PBX_TEST_CONTROL_KEY> restart` = new epoch + `core restart now`; `crash` = SIGKILL, the entrypoint restarts Asterisk with a new epoch like safe_asterisk) | none: never install anything like it on Issabel |
| Certificates | self-signed, generated at start (test only) | company CA or public certificate for HTTPS/WSS and AMI TLS |
| AMI ACL | private ranges | exactly the calling host IP |
| Secrets | test-only values in `docker-compose.yml` | vault; the calling service refuses the test values for any other PBX |
| Epoch hook | the entrypoint supervises Asterisk and writes a new UUID before every process start (container start or crash restart) | `pbx-epoch.sh` as a pre-start hook; safe_asterisk crash restarts are covered by the start time |
| Calling windows | 24/7 in the dialplan (tests must be deterministic) | the approved hours, the same as the app policy |
| CDR | `cdr_csv` to `Master.csv` (volume `cawp_pbx_test_cdr`, mounted read-only into the calling service); the full `uniqueid` is kept | MySQL `asteriskcdrdb.cdr`; `uniqueid` truncated to 32 characters, so `userfield` must be logged |
| Recordings | volume `cawp_pbx_test_recordings`; the simulated customers send no audio, so the files hold only the WAV header | real audio under `/var/spool/asterisk/monitor` |
| Codecs | ulaw/alaw | ulaw/alaw, or opus only with `codec_opus` |
| Dialplan | generated from `pbx-test/policy.json` (checked equal by the test suite) | generated from the production policy (trunk, windows, limits) |
| AMI event model (Alitqan) | Asterisk 20: `ChannelId` honoured (`cawp-<attempt>` is the agent Uniqueid), `Linkedid`, DialBegin/DialEnd, BridgeEnter/BridgeLeave, `Timestamp` | Asterisk 11.25 (AMI 1.3), `CALLING_PBX_EVENTS=legacy`: `ChannelId` ignored (Uniqueid `"<epoch>.<seq>"`), no `Linkedid`, `Dial` Begin/End (End has no DestUniqueID), `Bridge` Link/Unlink, no `Timestamp` unless `timestampevents=yes` (recommended; without it the service orders events by receipt). Agent leg bound by the Originate ActionID's `OriginateResponse`; the FastAGI hook allows only that channel (it waits up to 2.5 s for the binding, then `agent_leg_unbound`; `agent_leg_refused` / `originate_failed` / `agent_channel_mismatch` otherwise); customer legs from the agent's `Dial` Begin; UserEvents trusted only when their `ChanUniqueid` is a leg of the claimed attempt (a dialplan writer could forge it: keep dialplan write access to the operator). Covered by `tests/test_calling_legacy_pbx.py` with frames of the captured key sets, **not yet by a live call** (section 13). |
| Outbound (Alitqan) | loopback trunk, E.164 | GSM gateways `Mobily18`…`Mobily26` (chan_sip), the first reachable one (`SIPPEER` status `OK`) dialled once, national `05XXXXXXXX`: policy `trunks` + `dial_format: national_sa` |

## 16. Commissioning sheet (fill in)

- Issabel release, Asterisk version, channel driver(s), codec_opus yes/no:
- PBX FQDN used by browsers, certificate issuer and expiry:
- `http.conf` / `manager.conf` generated or static; where the includes were added:
- Trunk endpoint name, number format the carrier needs (`+E.164`, `9665…` or `05…`), caller ID set on the trunk:
- Epoch hook installed as (systemd drop-in / init script); epoch after restart A / after reload / after restart B:
- Calling windows approved by, and the policy JSON used:
- `pbx-verify.sh` result (attach the output), `calling.verify` result (attach):
- Acceptance gates 1–15: date, operator, result, and the attempt ids used:
- `CALLING_PBX_COMMISSIONED=1` set on (date) by:

## 4d. Short-lived SIP credentials for the in-app softphone (Phase 5 P3)

The Issabel equivalent (commissioning, not verified on Issabel)

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
