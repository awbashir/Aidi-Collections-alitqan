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

## Not configured yet

* Calling: no PBX settings (`CALLING_*`, `SIP_*`, `CDR_*`), no extensions, no calling window
  policy. The `calling` service is not started.
* AI: no provider key and no AI data policy, so AI features refuse.
* Contracts, portfolios and comment statuses: create them in the app, then link supervisors and
  collectors to their contracts.
* Backups: nothing schedules `pg_dump` yet.
