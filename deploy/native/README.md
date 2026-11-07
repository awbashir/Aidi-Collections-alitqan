# Native install (no Docker): Alitqan, `root@10.10.10.150`

The Alitqan server is an unprivileged Proxmox LXC (CT 102) without nesting, and the Proxmox host
cannot be changed, so Docker cannot start containers there. The same stack runs natively on
Ubuntu 24.04 with systemd instead (customer decision 2026-10-21). The application code, the
migrations and the runtime settings are the same as the Docker install; only the packaging differs.

| Piece | Docker install | Here |
| --- | --- | --- |
| PostgreSQL 16 | `cawp-db` | apt (PGDG) `postgresql-16`, cluster `16/main`, **UTF8 / en_US.UTF-8** (as the source database), `localhost` only; tuning in `/etc/postgresql/16/main/conf.d/alitqan.conf` |
| pg_restore for the old system's dumps | `postgres:18-alpine` | `postgresql-client-18` (`/usr/lib/postgresql/18/bin/pg_restore`): the dumps are pg_dump 18 archives |
| Redis | `cawp-redis` | apt `redis-server` 7.0, `/etc/redis/alitqan.conf` (= `alitqan-redis.conf`, `include`d from `redis.conf`) (no persistence, 768 MB LRU, loopback) |
| API | `cawp-api` | `alitqan-api.service`: the Dockerfile's gunicorn command on `127.0.0.1:9797` |
| Worker | `cawp-worker` | `alitqan-worker.service` (`python -m worker`) |
| Calling service | `cawp-calling` | `alitqan-calling.service` (`python -m calling`): live monitor + call control only (dispatch, campaigns, AGI off) |
| AMI transport | TLS 5039 | `alitqan-ami-tunnel.service`: SSH local forward `127.0.0.1:15038` → PBX `127.0.0.1:5038` (user `cawp-ami`, forward-only key, pinned host key) |
| Proxy + SPA | `cawp-proxy` | apt `nginx` 1.24, site `alitqan.nginx.conf` → `/etc/nginx/sites-available/alitqan` (HTTPS 443, port 80 redirects; rules in `alitqan-app.inc` → `/etc/nginx/snippets/`); SPA in `/var/www/alitqan` |
| Python | `python:3.11-slim` | Python 3.11 from `uv` in `/opt/alitqan-runtime/python`, venv `/opt/alitqan-runtime/venv` |
| Node (build only) | `node:22-alpine` | `/opt/alitqan-runtime/node` (official tarball, checksum verified) |

Paths: code `/opt/alitqan` (root-owned, read-only for the services), data `/var/lib/alitqan/{uploads,quality}`
(user `cawp`), secrets `/etc/alitqan/` (root:cawp 0640; never committed): `api.env` (the
`.env.docker` keys with `127.0.0.1` hosts, `UPLOAD_DIR`, `QUALITY_STORAGE_DIR`, `CAWP_DB_DIR`) and
`recordings_ed25519`, `ami_tunnel_ed25519` (cawp 0400) and `pbx_known_hosts`. The services run as the system user `cawp` with
`ProtectSystem=strict`; only `/var/lib/alitqan` is writable.

## Install (as done on 2026-10-21)

```bash
# packages
/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
apt-get install -y postgresql-16 postgresql-client-18 redis-server nginx build-essential libpq-dev locales
locale-gen en_US.UTF-8
pg_dropcluster --stop 16 main && pg_createcluster --locale en_US.UTF-8 -e UTF8 16 main   # only on a new server
cp alitqan-pg.conf /etc/postgresql/16/main/conf.d/alitqan.conf && systemctl start postgresql@16-main
sudo -u postgres createdb mydata

# restore the old system's dump, then the legacy scripts
sudo -u postgres /usr/lib/postgresql/18/bin/pg_restore -d mydata --no-owner --no-privileges -j 4 /opt/alitqan-dump/mydata.dump
#   (the "transaction_timeout" errors are harmless: a PostgreSQL 17+ setting)
for f in 01_performance_indexes 02_jsonb_attribute 03_last_comment; do
  sudo -u postgres psql -d mydata -v ON_ERROR_STOP=1 -q -f /opt/alitqan/db/$f.sql; done

# runtime
useradd --system --home-dir /var/lib/alitqan --shell /usr/sbin/nologin cawp
UV_PYTHON_INSTALL_DIR=/opt/alitqan-runtime/python uv python install 3.11
uv venv --python 3.11 /opt/alitqan-runtime/venv
VIRTUAL_ENV=/opt/alitqan-runtime/venv uv pip install -r /opt/alitqan/CAWP-API/requirements.txt

# migrations (0004..)
cd /opt/alitqan/CAWP-API && set -a && . /etc/alitqan/api.env && set +a
/opt/alitqan-runtime/venv/bin/python manage_db.py baseline && /opt/alitqan-runtime/venv/bin/python manage_db.py up

# services, SPA, nginx
cp /opt/alitqan/deploy/native/alitqan-*.service /etc/systemd/system/ && systemctl daemon-reload
systemctl enable --now alitqan-ami-tunnel alitqan-api alitqan-worker alitqan-calling
cd /opt/alitqan/CAWP-frontend && PATH=/opt/alitqan-runtime/node/bin:$PATH npm ci && VITE_API_URL=/api npm run build
rsync -a --delete build/ /var/www/alitqan/
cp /opt/alitqan/deploy/native/alitqan.nginx.conf /etc/nginx/sites-available/alitqan && cp /opt/alitqan/deploy/native/alitqan-app.inc /etc/nginx/snippets/
ln -sf /etc/nginx/sites-available/alitqan /etc/nginx/sites-enabled/alitqan && rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

## Update to a new release

```bash
rsync -a --delete --exclude node_modules <new tree>/ /opt/alitqan/      # keeps deploy/certs (excluded on the sender)
VIRTUAL_ENV=/opt/alitqan-runtime/venv uv pip install -r /opt/alitqan/CAWP-API/requirements.txt
cd /opt/alitqan/CAWP-API && set -a && . /etc/alitqan/api.env && set +a && /opt/alitqan-runtime/venv/bin/python manage_db.py up
systemctl restart alitqan-api alitqan-worker alitqan-calling
cd /opt/alitqan/CAWP-frontend && PATH=/opt/alitqan-runtime/node/bin:$PATH npm ci && VITE_API_URL=/api npm run build \
  && rsync -a --delete build/ /var/www/alitqan/
```

## Re-import a fresh dump before go-live

Everything typed into the new server during the parallel run is lost.

```bash
systemctl stop alitqan-api alitqan-worker alitqan-calling
sudo -u postgres dropdb mydata && sudo -u postgres createdb mydata
# then: pg_restore, the three legacy scripts, baseline + up (above), and start the services
```

## Checks

```bash
systemctl is-active alitqan-api alitqan-worker alitqan-calling alitqan-ami-tunnel nginx postgresql@16-main redis-server
curl -sk https://127.0.0.1/api/health                       # {"status":"ok","db":true,"redis":true,...}
journalctl -u alitqan-api -n 100 --no-pager
```

Backups: nothing schedules `pg_dump` yet.
