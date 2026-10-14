#!/usr/bin/env bash
# Run API pytest files and/or frontend tsc+eslint on the app server in throwaway containers.
# Usage: tools/dev/server_check.sh <tag> [api <pytest args...>] [-- fe <files to eslint...>]
#   tools/dev/server_check.sh statuses api tests/test_x.py tests/test_y.py
#   tools/dev/server_check.sh pay fe src/views/x.tsx src/services/y.ts
# Each <tag> gets its own copy, network and Postgres, so several checks can run at once.
# Prints: pytest tail, then tsc errors in the given files (pre-existing errors elsewhere are filtered), then eslint.
set -u
TAG=${1:?tag}; shift; MODE=${1:?api|fe}; shift
HOST=${CHECK_HOST:-aiserver@10.0.40.6}
DIR=/tmp/cawp-chk-$TAG; N=cawp-chk-net-$TAG; DB=cawp-chk-db-$TAG
cd "$(dirname "$0")/../.."
if [ "$MODE" = api ]; then
  ARGS=$(printf '%q ' "$@")
  COPYFILE_DISABLE=1 tar -czf - --exclude='__pycache__' --exclude=node_modules CAWP-API db 2>/dev/null | ssh -o ConnectTimeout=15 "$HOST" "
    docker run --rm -v /tmp:/t alpine rm -rf /t/cawp-chk-$TAG >/dev/null 2>&1; mkdir -p $DIR && tar -xzf - -C $DIR 2>/dev/null
    docker rm -f $DB >/dev/null 2>&1; docker network rm $N >/dev/null 2>&1; docker network create $N >/dev/null
    docker run -d --rm --name $DB --network $N --tmpfs /var/lib/postgresql/data -e POSTGRES_PASSWORD=postgres postgres:16-alpine postgres -c fsync=off -c synchronous_commit=off >/dev/null
    for i in \$(seq 1 30); do docker exec $DB pg_isready -U postgres -h 127.0.0.1 >/dev/null 2>&1 && break; sleep 1; done; sleep 2
    docker run --rm --network $N -v $DIR:/repo -w /repo/CAWP-API -e TEST_DATABASE_URL=postgresql://postgres:postgres@$DB:5432/postgres -e PYTHONDONTWRITEBYTECODE=1 --entrypoint sh cawp-api -c \"pip install -q --disable-pip-version-check --root-user-action=ignore -r requirements-dev.txt >/dev/null 2>&1; python -m pytest -p no:cacheprovider -q $ARGS > /repo/pytest.out 2>&1; echo PYTEST_EXIT=\$?; tail -60 /repo/pytest.out; echo SUMMARY:; grep -E '[0-9]+ (passed|failed|error)' /repo/pytest.out | tail -2\"
    docker rm -f $DB >/dev/null 2>&1; docker network rm $N >/dev/null 2>&1; docker run --rm -v /tmp:/t alpine rm -rf /t/cawp-chk-$TAG"
else
  FILES=$(printf '%s ' "$@"); PAT=$(printf '%s|' "$@" | sed 's/|$//; s/\./\\./g')
  COPYFILE_DISABLE=1 tar -czf - --exclude=node_modules --exclude=build CAWP-frontend 2>/dev/null | ssh -o ConnectTimeout=15 "$HOST" "
    docker run --rm -v /tmp:/t alpine rm -rf /t/cawp-chk-$TAG >/dev/null 2>&1; mkdir -p $DIR && tar -xzf - -C $DIR 2>/dev/null
    docker run --rm -v $DIR/CAWP-frontend:/app -w /app node:22-alpine sh -c 'npm ci --no-audit --no-fund >/dev/null 2>&1; echo == tsc errors in given files:; npx tsc --noEmit -p tsconfig.json 2>&1 | grep -E \"$PAT\" | head -40; echo == eslint:; npx eslint $FILES 2>&1 | tail -40; echo == done'
    docker run --rm -v /tmp:/t alpine rm -rf /t/cawp-chk-$TAG"
fi
