#!/bin/bash
# Build and run the API container from this directory.
#   ./run.sh [tag] [env-file]
# For the full local stack (postgres + redis + api) use the compose file one level up:
#   cd .. && docker compose up -d
set -euo pipefail

TAG="${1:-cawp-api:local}"
ENV_FILE="${2:-.env}"
NAME="${CONTAINER_NAME:-cawp-api}"
PORT="${PORT:-9797}"

if [ ! -f "$ENV_FILE" ]; then
  echo "env file '$ENV_FILE' not found - copy .env.example to .env first" >&2
  exit 1
fi

docker build -t "$TAG" .

docker rm -f "$NAME" 2>/dev/null || true

docker run -d --name "$NAME" --restart unless-stopped \
  --env-file "$ENV_FILE" \
  -p "${PORT}:${PORT}" \
  -v "$(pwd)/uploads:/app/uploads" \
  "$TAG"

# Schema changes ship as the reviewed SQL scripts in ../db (see DEPLOY.md).
# `flask db migrate` (autogenerate) must NOT be run: models.py declares every index that
# exists in the database, and autogenerate would try to "fix" what it cannot express.
# Hand-written revisions, if any, are applied with `upgrade`.
if [ -d migrations ]; then
  docker exec "$NAME" flask db upgrade
fi

docker exec "$NAME" curl -fsS "http://127.0.0.1:${PORT}/health" && echo
