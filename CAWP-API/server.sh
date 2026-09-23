#!/bin/bash
# Pull and run a published image on the server.
#   ./server.sh [image] [env-file]
set -euo pipefail

IMAGE="${1:-faniyatalitqan/cawp:latest}"
ENV_FILE="${2:-/etc/cawp/api.env}"
NAME="${CONTAINER_NAME:-cawp-api}"
PORT="${PORT:-9797}"
UPLOADS="${UPLOADS_DIR:-/srv/cawp/uploads}"

if [ ! -f "$ENV_FILE" ]; then
  echo "env file '$ENV_FILE' not found - see DEPLOY.md section 4" >&2
  exit 1
fi

docker pull "$IMAGE"
docker rm -f "$NAME" 2>/dev/null || true

mkdir -p "$UPLOADS"
docker run -d --name "$NAME" --restart unless-stopped \
  --env-file "$ENV_FILE" \
  -p "${PORT}:${PORT}" \
  -v "${UPLOADS}:/app/uploads" \
  "$IMAGE"

# The database scripts in ../db are applied by hand, once, per DEPLOY.md.
# NEVER run `flask db migrate` against production (see DEPLOY.md, "Alembic / flask db").
if [ -d migrations ]; then
  docker exec "$NAME" flask db upgrade
fi

until docker exec "$NAME" curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null; do
  echo "waiting for /health ..."; sleep 3
done
echo "cawp-api is healthy on :${PORT}"
