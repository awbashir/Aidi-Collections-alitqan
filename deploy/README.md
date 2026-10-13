# Single-server production install

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
| Restricted dialplan | PBX `collections-human.conf` (included from `extensions_custom.conf`), generated from driver `SIP`, trunk `ITC_SIP_Trunk`, 24/7, 3600 s, recording on |
| Calling window (app) | every day 00:00–24:00 (customer decision) |
| Inbound | every trunk call → ring group 600 (supervisors 103–107, 30 s) |
| Dialpad outside numbers (customer decision) | app `SIP_EXTERNAL_DIAL_PATTERN` (`.env.docker`, `$$` = `$`); PBX outbound route 1 patterns `0.`, `966XXXXXXXXX`, `800XXXXXXX`, `920XXXXXX` (plus the original `9` + number). Not case-linked: no calling-window check, no wrap-up; Issabel records them |
| Call records | read-only MySQL `cawp_cdr`@10.0.40.6 (`SELECT` on `asteriskcdrdb.cdr`) → `CDR_*` in `.env.docker`, `CALLING_CDR_SOURCE=mysql` |
| NTP | chrony → time.google.com, time.cloudflare.com |

Secrets live only in `deploy/prod.env` / `CAWP-API/.env.docker` (app server) and root:asterisk
0640 files on the PBX. Backups taken before the change: `/root/cawp-backup-asterisk-*.tgz`
and `/root/cawp-backup-asterisk-db-*.sql.gz` on the PBX. Checks: `python -m calling.verify`
(app server, `calling` image) and `/usr/local/sbin/cawp-pbx-verify.sh` (PBX).

## Not configured yet

* Recordings download (`RECORDINGS_SSH_*`) is off: the app cannot play Issabel recordings yet.
* Issabel's firewall is off: restrict 5038/5039/8088 to 10.0.40.6.
* AI: no provider key and no AI data policy, so AI features refuse.
* Contracts, portfolios and comment statuses: create them in the app, then link supervisors and
  collectors to their contracts.
* Backups: nothing schedules `pg_dump` yet.
