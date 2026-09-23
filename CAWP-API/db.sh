#!/bin/bash
# Apply the reviewed schema scripts (see DEPLOY.md for the locks each one takes).
#   ./db.sh [psql-connection-args...]
#   ./db.sh -U postgres -d mydata -h 127.0.0.1 -p 5433
# Defaults come from .env (DATABASE_URL) or the PG* environment variables.
set -euo pipefail

DB_DIR="$(cd "$(dirname "$0")/../db" && pwd)"
PSQL_ARGS=("$@")
if [ ${#PSQL_ARGS[@]} -eq 0 ]; then
  PSQL_ARGS=(-U "${PGUSER:-postgres}" -d "${PGDATABASE:-mydata}")
fi

# CREATE INDEX CONCURRENTLY cannot run inside a transaction block, so no -1/--single-transaction.
for f in 01_performance_indexes.sql 02_jsonb_attribute.sql 03_last_comment.sql; do
  echo "=== $f"
  psql "${PSQL_ARGS[@]}" -v ON_ERROR_STOP=0 -f "$DB_DIR/$f"
done

# `flask db migrate` is deliberately absent: models.py already declares every index and
# autogenerate would emit destructive DDL. Hand-written revisions only:
#   flask db upgrade
