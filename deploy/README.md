# Single-server production install

## Alitqan: `root@10.10.10.150`, restored from the old system (native install)

Installed 2026-10-21 for Alitqan from the old system's database (`dump-mydata-202610211450.sql`,
pg_dump 18 custom format of PostgreSQL 16.3: 578,344 cases, 1.43M notes, 2.38M contacts, 37,369
payments, 308 users). **Runs natively with systemd, not Docker**: the server is an unprivileged
Proxmox LXC (CT 102) without nesting and the host cannot be changed. Runbook:
[`native/README.md`](native/README.md). The old system (10.10.10.240) keeps running in parallel;
before go-live take a fresh dump and re-import (same runbook).

| Item | Value |
| --- | --- |
| URL | `https://10.10.10.150` (port 80 redirects to it), self-signed certificate (`/opt/alitqan/deploy/certs/`, made on the server, SAN IP 10.10.10.150, until 2029-01-23). The `alitqan_com_sa.crt` in the frontend tree expired on 2025-10-07 and only names the website |
| PBX | Issabel `10.10.0.220` (CentOS 7, Asterisk 11.25.3, **chan_sip only, no PJSIP**), production: never restart or reload it without approval |
| CDR | MySQL user `cawp_ro@10.10.10.150`, `SELECT` on `asteriskcdrdb.*` and `call_center.*` only (created 2026-10-21, online). `cdr` has no `calldate` index (2.6M rows) |
| Recordings | PBX user `cawp-rec` (password locked) whose only key (`/etc/alitqan/recordings_ed25519` here) is `restrict,from="10.10.10.150",command="/usr/libexec/openssh/sftp-server -R"`: read-only SFTP, no shell; the files are world-readable, so no group. `RECORDINGS_READER=sftp`. Nothing on the PBX was reloaded |
| Live monitor + listen / whisper / join | **commissioned 2026-10-21** (customer approval; reloads only, no restart; active calls unaffected). See "Alitqan PBX changes" below |
| Click-to-call / campaigns | **not commissioned**: the attempt evidence relies on Asterisk 12+ AMI events (`BridgeEnter`, `DialBegin`, `Linkedid`, Originate `ChannelId`), so it needs porting to Asterisk 11 first. The calling service runs with `CALLING_DISPATCH_ENABLED=0`, `CALLING_CAMPAIGNS_ENABLED=0`, `CALLING_AGI_ENABLED=0`, so `POST /v2/calls` answers 503 `transport_not_connected` |
| Softphone | **off for every user by default** (users call from their own VoIP phones, migration 0082). Enabling it for a user (Extensions page) converts their extension to a browser (WebRTC) extension on the PBX, and their desk phone stops until it is switched off, which restores the extension exactly. Installed and accepted on 2026-10-21 with Chrome: [`docs/alitqan-webrtc-softphone-plan.md`](../docs/alitqan-webrtc-softphone-plan.md) §7 |
| Roles | the merged new permission keys are `false` for every role except ADMIN: grant `calls` / `calls_make` (collectors) and `calls_supervise` (supervisors) in Roles before calling |
| Extensions | `user_extensions` is empty: adopt the legacy `users.extension` values (Calling admin → legacy drift) |

The Docker files in this folder were also sized for this server (8 GB RAM) but are not used here.

### Alitqan PBX changes (Issabel 10.10.0.220, 2026-10-21)

Backup before any change: `/root/cawp-backup-alitqan-20261021-201204/` on the PBX (`etc-asterisk.tgz` = all of `/etc/asterisk`,
`before-status.txt`, `passwd`/`group`/`shadow`; `after-files.tgz` = the changed files).

| Change | Where | Reload |
| --- | --- | --- |
| Read-only CDR user `cawp_ro@10.10.10.150` (`SELECT` on `asteriskcdrdb.*`, `call_center.*`) | MariaDB | none |
| Recordings user `cawp-rec` (locked; key `restrict,from="10.10.10.150",command="/usr/libexec/openssh/sftp-server -R"`) | `/home/cawp-rec/.ssh/authorized_keys` | none |
| AMI tunnel user `cawp-ami` (locked, `/sbin/nologin`; key `restrict,port-forwarding,permitopen="127.0.0.1:5038",from="10.10.10.150",command="/sbin/nologin"`) | `/home/cawp-ami/.ssh/authorized_keys` | none |
| AMI user `[cawp-calling]`: `permit=127.0.0.1/32` (the tunnel), `read=call,user,system`, `write=originate,reporting,call`. AMI `[general]` untouched (no `timestampevents`: only click-to-call evidence uses it) | appended to `/etc/asterisk/manager_custom.conf` | `manager reload` |
| Call-control dialplan (`python -m services.calling.callctl_dialplan` with `{"driver":"SIP","record":true,"max_call_seconds":3600,"ring_seconds":30,"recall_seconds":20}`): contexts `cawp-callctl-*`, reached only by AMI Redirect / Originate | `/etc/asterisk/cawp-callctl.conf` (root:asterisk 0640), `#include` appended to `extensions_custom.conf` | `dialplan reload` |
| ConfBridge profiles `cawp_room`, `cawp_customer`, `cawp_member` (`--confbridge`) | `/etc/asterisk/confbridge_custom.conf` (was empty) | `module reload app_confbridge.so` |
| PBX epoch global, written once (no pre-start hook: Asterisk's start script is untouched; a restart still changes the epoch through the process start time) | `/etc/asterisk/collections-pbx-epoch.conf`, `#include` in `globals_custom.conf` | `dialplan reload` |

App server: `alitqan-ami-tunnel.service` (`127.0.0.1:15038` → PBX `127.0.0.1:5038`, host key pinned in
`/etc/alitqan/pbx_known_hosts`); `/etc/alitqan/api.env`: `CALLING_ENABLED=1`, `CALLING_PBX_COMMISSIONED=1`,
`CALLING_AMI_HOST=127.0.0.1`, `CALLING_AMI_PORT=15038`, `CALLING_AMI_TLS=0`, `CALLING_AMI_USERNAME=cawp-calling`,
`CALLING_AMI_SECRET`, `CALLING_DRIVER=SIP`, dispatch / campaigns / AGI off, `CALLING_CONTROL_ENABLED=1`.

Code fixes found on this PBX: Asterisk 11 writes `channelvars` as `ChanVariable(<channel>): …` (the
AMI decoder now accepts exactly that and normalises it); its `CoreShowChannel` has no `Linkedid` /
`BridgeId` (two-party bridges are paired from `BridgedUniqueID`); the monitor drops the dialer's
`OutgoingSpoolFailed` pseudo channels, treats trunk legs being dialed as outbound, and joins
`Local/…;1/;2` queue pairs to their call.

**Still to check with a real call** (needs a supervisor and an agent, each with an app extension):
listen → whisper → join → stop on an agent's live call; that `X-CAWP-Control` reaches a softphone
(`sip set debug peer <ext>`); transfers and conferences.

**Rollback** (in order): `systemctl stop alitqan-calling alitqan-ami-tunnel` (app server); on the
PBX remove the `[cawp-calling]` block from `manager_custom.conf`, the `#include cawp-callctl.conf`
line from `extensions_custom.conf` and the epoch include from `globals_custom.conf`, empty
`confbridge_custom.conf`, then `manager reload`, `dialplan reload`, `module reload app_confbridge.so`;
`userdel -r cawp-ami` / `cawp-rec`; `DROP USER 'cawp_ro'@'10.10.10.150'`. Or restore the files from
`etc-asterisk.tgz` in the backup and run the same reloads.

---

First installed on `aiserver@10.0.40.6` (`~/cawp`, Ubuntu 24.04) on 2026-10-11 as a clean
installation for `https://aicallcenter.a-alaoula.com`.

| File | What |
| --- | --- |
| `docker-compose.prod.yml` | override: no host ports for db / redis / api, code baked into images, Postgres password from `prod.env`, more Postgres memory |
| `nginx/cawp.conf` | TLS, the built SPA, `/api/*` → `api:9797` with the prefix stripped (the `proxy` service, profile `prod`, in `../docker-compose.yml`) |
| `seed/lookups.sql` | generic lookup lists for an empty database (contact methods, reachability, collection methods/types, contact types, archive and delinquency reasons, update sides). No customer, contract, status or user rows |
| `provision_users.py` | first roles (`SuperAdmin`, `مشرف`, `محصل`), branches, nationalities, users and supervisor links from a JSON file; refuses unless `users` is empty |

Never committed, only on the server: `deploy/certs/{fullchain,privkey}.pem`, `deploy/prod.env`
(`POSTGRES_PASSWORD`), `CAWP-API/.env.docker` (database URL, `SECRET_KEY`, `JWT_SECRET_KEY`,
`CORS_ORIGINS`, `CDR_ENABLED=0`).

```bash
C="docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml --env-file deploy/prod.env --profile prod"
```

## Clean install (empty database)

```bash
$C build
$C up -d db redis                                   # wait until cawp-db is healthy
$C run --rm --no-deps api python manage_db.py load-schema /db/schema/baseline.sql
$C run --rm --no-deps api python manage_db.py baseline
$C run --rm --no-deps api python manage_db.py up
docker exec -i cawp-db psql -U postgres -d mydata -v ON_ERROR_STOP=1 -q < deploy/seed/lookups.sql
$C run --rm --no-deps -v /tmp/users.json:/provision/users.json:ro \
   -v $PWD/deploy/provision_users.py:/provision/provision_users.py:ro \
   api python /provision/provision_users.py /provision/users.json   # then delete /tmp/users.json
$C up -d
```

`load-schema` refuses a database that has any table, including the `schema_migrations` that
`status` creates. Run it before anything else touches the database.

## Update to a new release

Copy the new tree over `~/cawp` (keep `deploy/certs`, `deploy/prod.env`, `CAWP-API/.env.docker`), then:

```bash
$C build
$C run --rm --no-deps api python manage_db.py up
$C up -d
```

## Renew the certificate

The current `*.a-alaoula.com` certificate expires on 2027-01-16. Replace the two files in
`deploy/certs/` and run `docker exec cawp-proxy nginx -s reload`.

## Calling: Issabel 10.0.40.7 (commissioned 2026-10-11)

Issabel 5 / Asterisk 18.19 (Rocky 8). Agents are PJSIP WebRTC extensions; the carrier trunks
`ITC_SIP_Trunk` / `ITC_SIP_Trunk_Backup` are chan_sip. Runbook: `docs/issabel-commissioning.md`.

| Piece | Where |
| --- | --- |
| Extensions 100–135, one per app user (Excel order; aidi = 100) | IssabelPBX (created with its core functions); assigned in the app (`user_extensions`) |
| WebRTC settings, `allow_transfer=no`, realtime `auth=<ext>` + locked fallback auth | PBX `/etc/asterisk/pjsip_custom_post.conf` |
| Softphone passwords (10 min) read from the app DB | PBX `sorcery.conf`, `extconfig.conf`, `res_odbc_custom.conf`, `/etc/odbc.ini` DSN `cawp` → view `pbx_ps_auths` as `cawp_pbx_realtime` over TLS; app `pg_hba.conf` admits that role from 10.0.40.7 only |
| Browser signalling | `wss://aicollection.a-alaoula.com/pbx-ws` → nginx → `http://10.0.40.7:8088/ws` (`nginx/cawp.conf`) |
| AMI | TLS 5039 with the wildcard cert (`/etc/asterisk/keys/cawp-pbx.*`), user `cawp-calling` (originate + reporting, from 10.0.40.6 only) in `manager_custom.conf`; client verifies the name `pbx.a-alaoula.com` |
| PBX epoch | `/usr/local/sbin/cawp-pbx-epoch.sh` as systemd `ExecStartPre` (`asterisk.service.d/cawp-epoch.conf`) |
| AGI hook + key | PBX `collections-globals.conf` (included from `globals_custom.conf`) → `agi://10.0.40.6:4573` (the `calling` service) |
| Restricted dialplan | PBX `collections-human.conf` (included from `extensions_custom.conf`), generated from driver `SIP`, trunk `ITC_SIP_Trunk`, 24/7, 3600 s, recording on, `caller_id_gosub: cawp-cid-rotate`, `post_call_context: cawp-survey` |
| Calling window (app) | every day 00:00–24:00 (customer decision) |
| Outgoing caller ID | rotates through the 28 ITC numbers (966125123077–100, 966125148577–579, 966125148660): pool in PBX `cawp-cid-pool.conf` (`CAWP_CID_POOL`), counter AstDB `CAWP/cid_rr`; dial pad via the ITC pre-dial hook in `extensions_override_issabelpbx.conf`, click-to-call via `cawp-cid-rotate.conf` (policy `caller_id_gosub`). The trunks no longer force `fromuser` |
| Inbound | every trunk call → ring group 600 (supervisors 103–107, 30 s), except these, which go to the same IVR `cawp-ivr-800`: the toll-free number (DIDs `+9668002440414`, `9668002440414`, `8002440414`) and, since 2026-10-14, the local lines 966125123075 and 966125123076 (DIDs with and without `+`). The IVR tags each call `CDR(userfield)=ivr:<DID>…` and records to `in-ivr-<uniqueid>.wav` |
| Toll-free IVR | PBX `cawp-ivr.conf` (included from `extensions_custom.conf`): welcome + language (1 Arabic / 2 English) → department menu (all options ring `CAWP_IVR_TARGETS`, the supervisors, for now) → "call is recorded" → MixMonitor → "please hold" → Dial (`g`). Prompts `/var/lib/asterisk/sounds/cawp/*.wav` (Script_01, working hours, 8 kHz mono); settings `cawp-ivr-globals.conf` |
| Post-call survey | per-contract switch in the app (Calling admin → Post-call survey; `contract_call_settings`, migration 0066). When the agent hangs up first the customer goes to PBX context `cawp-survey`: click-to-call via Dial `F(cawp-survey^s^1)` (policy `post_call_context`), the IVR after its Dial. It asks FastAGI `collections-survey-offer` (eligible = the case's contract has it on; incoming callers matched by number), plays `{ar,en}-survey` and sends the digit to `collections-survey-result` (`call_surveys`) |
| Dialpad outside numbers (customer decision) | app `SIP_EXTERNAL_DIAL_PATTERN` (`.env.docker`, `$$` = `$`); PBX outbound route 1 patterns `0.`, `966XXXXXXXXX`, `800XXXXXXX`, `920XXXXXX` (plus the original `9` + number). Not case-linked: no calling-window check, no wrap-up; Issabel records them |
| Call records | read-only MySQL `cawp_cdr`@10.0.40.6 (`SELECT` on `asteriskcdrdb.cdr`) → `CDR_*` in `.env.docker`, `CALLING_CDR_SOURCE=mysql`; `CDR_CALL_CENTER=0` (no Issabel call-center module) |
| Recordings (2026-10-17) | Read-only file server on the PBX: Apache `Listen 8091`, its own VirtualHost in `/etc/httpd/conf.d/cawp-recordings.conf` (the Issabel UI on 80/443 is untouched). It serves `/var/spool/asterisk/monitor` only: one file per GET/HEAD (`YYYY/MM/DD/<name>.wav|gsm|mp3|ogg`), Range supported, no listing, no symlinks, everything else 403. It requires `Require ip 10.0.40.6` AND the header `X-CAWP-Token`. The secret is in root-only `/etc/httpd/cawp/recordings-token.conf` (`Define CAWP_RECORDINGS_TOKEN …`) and in `.env.docker` as `RECORDINGS_HTTP_TOKEN`. Logs `/var/log/httpd/cawp_recordings_*_log` (no headers). App: `RECORDINGS_READER=http`, `RECORDINGS_HTTP_BASE=http://10.0.40.7:8091/` (`services/calling_cdr.py`). SELinux is disabled and firewalld/iptables are off on the PBX, so the IP rule and the token are the only guard; add a firewall rule when the PBX firewall is turned on. Backup: `/root/cawp-backup-httpd-*.tgz` |
| Extension provisioning (2026-10-20) | The app creates / renames / deletes agent extensions ("التحويلات" page; new users get one automatically, migration 0077). PBX: Apache `Listen 8092`, `/etc/httpd/conf.d/cawp-provision.conf` → `/var/www/cawp-provision/index.php` (php-fpm, user asterisk) → `sudo /usr/local/sbin/cawp-ext-provision` (root, `/etc/sudoers.d/cawp-ext-provision`). Same guard as 8091: `Require ip 10.0.40.6` AND `X-CAWP-Token` (root-only `/etc/httpd/cawp/provision-token.conf`; app: `PBX_PROVISION_URL=http://10.0.40.7:8092/`, `PBX_PROVISION_TOKEN` in `.env.docker`). The script replays the manual procedure (IssabelPBX `core_users_add` / `core_devices_add` with extension 100's settings, AstDB recording keys, retrieve_conf, `core reload`), extensions 100–199 only, one lock, a backup per change in `/root/cawp-backup-ext/`, log `/var/log/cawp-ext-provision.log`. The WebRTC override blocks of new extensions are copied from 100's blocks into `/etc/asterisk/pjsip_cawp_managed.conf` (`#include`d at the end of `pjsip_custom_post.conf`; 100–140 stay where they were). It deletes only extensions it created, with no active channel and no ring group. Sources: `CAWP-API/calling/issabel/ext-provision/` |
| NTP | chrony → time.google.com, time.cloudflare.com |

Secrets live only in `deploy/prod.env` / `CAWP-API/.env.docker` (app server) and root:asterisk
0640 files on the PBX. Backups taken before the change: `/root/cawp-backup-asterisk-*.tgz`
and `/root/cawp-backup-asterisk-db-*.sql.gz` on the PBX. Checks: `python -m calling.verify`
(app server, `calling` image) and `/usr/local/sbin/cawp-pbx-verify.sh` (PBX).

## Not configured yet

* Issabel's firewall is off: restrict 5038/5039/8088 (and 8091 recordings, 8092 extension provisioning) to 10.0.40.6.
* AI: no provider key and no AI data policy, so AI features refuse.
* Contracts, portfolios and comment statuses: create them in the app, then link supervisors and
  collectors to their contracts.
* Backups: nothing schedules `pg_dump` yet.
