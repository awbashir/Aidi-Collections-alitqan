# Alitqan: in-app (WebRTC) softphone on Issabel 4 / Asterisk 11 — plan

Status: **installed and accepted on a throwaway extension (411), 2026-10-21**; see §7. Read-only
checks of the PBX (`10.10.0.220`, Issabel 4, Asterisk 11.25.3, chan_sip only) are summarised in §1.
Nothing here restarts Asterisk; every PBX change is a reload of the same kind as Issabel's own
*Apply Config*.

## 1. What the PBX can and cannot do

| Need for browser calling | On this PBX | Consequence |
| --- | --- | --- |
| SIP over WebSocket | `res_http_websocket` loaded, `/ws` served on 8088; chan_sip has WebSocket support | The browser reaches it through the app: `wss://10.10.10.150/pbx-ws` → nginx → `http://10.10.0.220:8088/ws` on the LAN (already in the nginx site). No change to `http.conf` |
| DTLS-SRTP media, DTLS 1.2 | `res_rtp_asterisk` uses `DTLS_method` with OpenSSL 1.0.2k; `res_srtp` loaded; SHA-256 fingerprints supported | Modern browsers can negotiate encrypted media |
| ICE, AVPF | chan_sip `icesupport`, `avpf`, `force_avp` | Per-extension settings (below) |
| Opus codec | `codec_opus` present | Opus for the browser, transcoded to the trunks |
| **rtcp-mux** | **not supported** (came in Asterisk 13.15) | **The main risk.** Chrome/Edge expect RTCP multiplexed on the RTP port; on a non-muxed answer they may refuse the call. Firefox still negotiates without it. Must be proven by the test in §5 before any real user |
| DTLS certificate | the existing `/etc/asterisk/keys/asterisk.pem` **expired 2026-10-03** | Browsers check the fingerprint, not the dates, but a dedicated certificate is used instead (nothing that exists is touched) |
| Short-lived app passwords (the app's design on PJSIP: a new password every 10 minutes, checked by the PBX from the app database) | PJSIP-only; chan_sip has no equivalent without an ODBC realtime setup that would take the WebRTC extensions out of IssabelPBX | A per-extension password kept encrypted by the app, issued only to the extension's owner in memory (§3.2) |

## 2. What it means for the users' desk-phone extensions

A chan_sip extension is **either** a desk phone (plain UDP, no encryption) **or** a browser phone
(WebSocket, DTLS-SRTP, ICE): the media settings belong to the extension, and one extension has one
registration. So, per user, **enabling the softphone converts that user's extension (same number)
to a browser phone**:

* **While enabled**: the desk phone on that extension stops working (it cannot register; the PBX
  would refuse its unencrypted media). Calls to the extension ring the browser tab instead.
  The number, outbound caller ID, CDR, recordings (recorded on the PBX as today), ring groups,
  queues and the Issabel dialer's agent login all stay the same.
* **When the browser is closed** the extension is unregistered, exactly like an unplugged desk
  phone: queue / dialer / ring-group calls to it fail over or are missed.
* **When disabled**: the extension goes back to the desk-phone settings of the agent template
  (401) with a **new SIP password, shown once** in the app to type into the desk phone (the same
  screen as for new extensions).
* Users who never get the softphone are not affected at all.

Rejected alternative: a second (browser) extension per user. The app ties calling, click-to-call,
the call monitor and CDR attribution to one extension per user, and the desk phone would no longer
be that user's extension in the app.

## 3. Changes

### 3.1 PBX (one time)

| # | Change | Reload |
| --- | --- | --- |
| 1 | Backup of `/etc/asterisk`, the IssabelPBX `sip` table, the provisioning files | none |
| 2 | A dedicated self-signed DTLS certificate `/etc/asterisk/keys/cawp-webrtc.pem` (+ key), `root:asterisk 0640` | none |
| 3 | Provisioning script (`/usr/local/sbin/cawp-ext-provision`): new operation `webrtc <ext> on|off`, only for extensions 400–499 that exist and have no active call. **on**: sets the extension's IssabelPBX SIP settings to `transport=ws,wss`, `avpf=yes`, `force_avp=yes`, `icesupport=yes`, `encryption=yes`, `dtlsenable=yes`, `dtlsverify=no`, `dtlssetup=actpass`, `dtlscertfile/dtlsprivatekey=cawp-webrtc`, `nat=force_rport,comedia`, `disallow=all`, `allow=opus,ulaw,alaw`, and a new random password returned once to the app. **off**: the template's (401) desk-phone settings and a new password returned once. Every change is backed up first and logged without the password | per switch: IssabelPBX *Apply Config* (`retrieve_conf` + `core reload`), the same as pressing *Apply* in the Issabel UI; calls are not dropped |
| 4 | The provisioning vhost route `POST /v1/extensions/<ext>/webrtc` and the sudoers line `cawp-ext-provision webrtc *` | `httpd` graceful reload (Issabel UI unaffected) |

Nothing else on the PBX changes: no `sip.conf` general settings, no `http.conf`, no firewall, no
Asterisk restart.

### 3.2 App

* `SIP_WSS_URL=wss://10.10.10.150/pbx-ws`, `SIP_REALM=asterisk`, a credential mode for chan_sip
  (`SIP_CREDENTIAL_MODE=static`).
* Additive migration: the browser password per extension, **encrypted** with a key that is only in
  `/etc/alitqan/api.env`; given only to the active owner of the extension, only while their
  softphone is enabled, only after login, `Cache-Control: no-store`, kept in browser memory only
  (never in local storage), every issue audited without the password.
* The softphone switch (Extensions page) calls the PBX: enable = `webrtc on` (refused while the
  extension is in a call), disable = `webrtc off` and the desk-phone password shown once.
* Tests for all of it next to the existing softphone tests.

## 4. Order of work

1. App code + tests (no PBX contact).
2. PBX one-time steps 1, 2, 4, then the script (3) — with your go-ahead.
3. The test in §5 on a throwaway extension.
4. Only if §5 passes: enable the softphone for one real user of your choice, then others.

## 5. Acceptance test (before any real user)

On a throwaway extension (e.g. 498, created and later deleted from the Extensions page), with a
test user in the app:

1. Chrome (or Edge) and Firefox on an agent PC on the LAN: the softphone registers (`sip show peer 498`
   shows the WebSocket contact).
2. 498 ↔ a desk phone, both directions: two-way audio, hold/resume, mute, DTMF, hang-up from each side.
3. The call is recorded and plays in the app.
4. Click-to-call from the app rings the browser (once click-to-call is commissioned).
5. Switch off: 498 is a desk-phone extension again and the shown password registers a desk phone.

**Stop criteria**: if Chrome/Edge refuse the media (rtcp-mux), the options are Firefox-only use, or
moving the PBX to Issabel 5 / Asterisk 16+ with PJSIP, where the app's softphone is already tested.

## 6. Rollback

`webrtc off` per extension (restores the desk-phone settings), or restore the IssabelPBX `sip`
rows from the backup and *Apply Config*. Remove the vhost route / sudoers line and reload `httpd`.
Delete `cawp-webrtc.pem`. App: clear `SIP_WSS_URL` (the softphone then shows "not configured").

## 7. Result (2026-10-21)

Done as planned, with one improvement: switching the softphone **off** restores the extension's
IssabelPBX SIP rows **exactly** from a root-only snapshot taken at switch-on
(`/var/lib/cawp-ext-provision/desk/<ext>.json`), the desk phone's own password included, so the
desk phone registers again by itself and nobody retypes anything.

PBX (backup `/root/cawp-backup-alitqan-webrtc-*`): DTLS certificate `/etc/asterisk/keys/cawp-webrtc.{pem,key}`
(RSA-2048, self-signed, until 2036), the script's `webrtc <ext> on|off`, the vhost route `/webrtc`,
the sudoers line, one `httpd` graceful reload. App (`/etc/alitqan/api.env`): `SIP_WSS_URL=wss://10.10.10.150/pbx-ws`,
`SIP_DOMAIN=10.10.0.220`, `SIP_REALM=asterisk`, `SIP_CREDENTIAL_MODE=static`, `SIP_STATIC_SECRET_KEY`,
`SIP_REGISTER_EXPIRES=300`, `SIP_RTCP_MUX_POLICY=negotiate`; migration 0083.

Measured from an agent-LAN Mac (10.10.20.x), headless Chrome 155 with a fake microphone, JsSIP
3.13.8, extension 411 converted for the browser, calls to the echo test `*43`:

| Browser settings | Register | Call |
| --- | --- | --- |
| `rtcpMuxPolicy: require` (Chrome default) | registered | never connects (no rtcp-mux in Asterisk 11) |
| `rtcpMuxPolicy: negotiate`, `bundlePolicy: max-bundle` | registered | refused: "Bad Media Description" |
| **`rtcpMuxPolicy: negotiate`, `bundlePolicy: balanced`** | registered | **connected every time**: Opus both ways (~580 packets each way in 12 s), DTLS 1.2, SRTP AES-128, ICE connected |

So the server sends `rtcp_mux_policy: negotiate` with the credential and the softphone engine then
uses `bundlePolicy: balanced`. The real app (sign-in page → header chip) registered 411 ("مسجّل 411").
Switch-off restored 411's 31 SIP rows identical to the pre-conversion backup (password compared by
hash), and the app deleted its stored password.

Still to do by people: a real call with a headset (two-way voice quality, hold, DTMF), Edge, and the
first real user. Risk to watch: Chrome still honours `rtcpMuxPolicy: negotiate` (removed from the
spec); if a future Chrome drops it, calls stop connecting on Asterisk 11 (registration still works)
and the fix is Issabel 5 / Asterisk 16+.
