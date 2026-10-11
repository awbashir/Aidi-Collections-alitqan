#!/usr/bin/env bash
# One-command local dev run: database (restored from the dump on first run), migrations,
# the dev admin, API + worker, and optionally the frontend. Safe to re-run: every step
# checks before it acts, and nothing is ever dropped.
#
#   tools/dev-up.sh            # db, redis, api, worker + dev admin
#   tools/dev-up.sh --web      # + frontend dev server in Docker on :5177
#   tools/dev-up.sh --tailnet  # --web, reachable from your other Tailscale devices
#                              # (+ HTTPS via `tailscale serve` when the tailnet has HTTPS on)
#
# Dev admin: DEV_ADMIN_USER / DEV_ADMIN_PASSWORD (default aidi.dev / Aidi@2026).
set -euo pipefail

cd "$(dirname "$0")/.."
WEB=0
TAILNET=0
for arg in "$@"; do
  case "$arg" in
    --web) WEB=1 ;;
    --tailnet) WEB=1; TAILNET=1 ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

DUMP=dump-mydata-202609151932.sql
DEV_ADMIN_USER="${DEV_ADMIN_USER:-aidi.dev}"
DEV_ADMIN_PASSWORD="${DEV_ADMIN_PASSWORD:-Aidi@2026}"
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
psql_db() { docker exec cawp-db psql -U postgres -d mydata -v ON_ERROR_STOP=1 "$@"; }

docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and re-run." >&2; exit 1; }

step "Postgres + Redis"
docker compose up -d --wait db redis

if [ "$(psql_db -Atc "SELECT to_regclass('public.users') IS NOT NULL")" != "t" ]; then
  [ -f "$DUMP" ] || { echo "empty database and no $DUMP to restore from" >&2; exit 1; }
  step "Empty database: restoring $DUMP (first run only, a few minutes)"
  docker exec cawp-db pg_restore -U postgres -d mydata --no-owner --no-privileges -j 4 /dump/mydata.dump
  step "Legacy scripts 01-03 (indexes, jsonb attribute, last comment)"
  for f in 01_performance_indexes 02_jsonb_attribute 03_last_comment; do
    echo "  $f.sql"
    psql_db -q -f "/db/$f.sql" >/dev/null
  done
fi

step "API image + container"
docker compose up -d --build --wait api

step "Migrations"
docker exec cawp-api python manage_db.py baseline
docker exec cawp-api python manage_db.py up

step "Dev admin"
docker exec -e DEV_ADMIN_USER="$DEV_ADMIN_USER" -e DEV_ADMIN_PASSWORD="$DEV_ADMIN_PASSWORD" \
  cawp-api python dev_seed.py

step "Restart API (no auto-reload) + worker"
docker compose restart api >/dev/null
docker compose up -d --wait api worker

if [ "$WEB" = 1 ]; then
  step "Frontend (Docker, :5177)"
  if [ "$TAILNET" = 1 ]; then
    export VITE_API_URL=/api     # same-origin: vite.config.ts proxies /api to the api service
  fi
  docker compose --profile web up -d web
fi

TS_URLS=""
if [ "$TAILNET" = 1 ]; then
  step "Tailscale"
  TS=$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)
  if ! ts_json=$("$TS" status --self --json 2>/dev/null); then
    echo "  Tailscale is not running or not logged in; the frontend is still on :5177 for this machine." >&2
  else
    read -r TS_NAME TS_IP TS_HTTPS < <(printf '%s' "$ts_json" | python3 -c '
import json, sys
d = json.load(sys.stdin); s = d["Self"]
ip4 = next((ip for ip in s.get("TailscaleIPs", []) if "." in ip), "")
print(s["DNSName"].rstrip("."), ip4, "yes" if d.get("CertDomains") else "no")')
    TS_URLS="http://$TS_NAME:5177  or  http://$TS_IP:5177"
    if [ "$TS_HTTPS" = yes ]; then
      "$TS" serve --bg --https=443 http://localhost:5177 >/dev/null
      TS_URLS="https://$TS_NAME  (HTTPS: softphone + voice lab work)
            $TS_URLS"
    else
      echo "  HTTPS is off for this tailnet, so the softphone and voice lab (microphone) only work on this"
      echo "  machine. To enable: https://login.tailscale.com/admin/dns -> HTTPS Certificates, then re-run."
    fi
  fi
fi

step "Smoke check"
curl -fsS http://localhost:9797/health >/dev/null && echo "  /health ok"
body=$(printf '{"username":"%s","password":"%s"}' "$DEV_ADMIN_USER" "$DEV_ADMIN_PASSWORD")
if curl -fsS -H 'Content-Type: application/json' -d "$body" http://localhost:9797/login | grep -q '"access_token"'; then
  echo "  login as $DEV_ADMIN_USER ok"
else
  echo "  login as $DEV_ADMIN_USER FAILED" >&2; exit 1
fi
if [ "$TAILNET" = 1 ] && [ -n "${TS_IP:-}" ]; then
  # the web container runs npm ci on its first start: allow a few minutes
  for _ in $(seq 1 90); do
    curl -fsS -o /dev/null "http://$TS_IP:5177/api/health" 2>/dev/null && break
    sleep 2
  done
  if curl -fsS -H 'Content-Type: application/json' -d "$body" "http://$TS_IP:5177/api/login" | grep -q '"access_token"'; then
    echo "  login through http://$TS_IP:5177/api ok"
  else
    echo "  login through http://$TS_IP:5177/api FAILED (docker logs cawp-web)" >&2; exit 1
  fi
fi

cat <<EOF

Ready.
  API       http://localhost:9797
  Frontend  $( [ "$WEB" = 1 ] && echo "http://localhost:5177 (first start runs npm ci, give it a minute)" \
                            || echo "cd CAWP-frontend && npm ci && npx vite --port 5177   (or re-run with --web)")
$( [ -n "$TS_URLS" ] && echo "  Tailnet   $TS_URLS")
  Login     $DEV_ADMIN_USER / $DEV_ADMIN_PASSWORD   (local only)
EOF
