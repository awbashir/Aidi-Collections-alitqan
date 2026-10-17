#!/usr/bin/env bash
# server_check.sh api variant that also starts the CDR MySQL fixture (alias cdr-test-db) in the throwaway network.
set -u
TAG=${1:?tag}; shift
HOST=aiserver@10.0.40.6
DIR=/tmp/cawp-chk-$TAG; N=cawp-chk-net-$TAG; DB=cawp-chk-db-$TAG; MY=cawp-chk-my-$TAG
cd /Users/awbashir/Development/Aidi-Collections-new
ARGS=$(printf '%q ' "$@")
COPYFILE_DISABLE=1 tar -czf - --exclude='__pycache__' --exclude=node_modules CAWP-API db 2>/dev/null | ssh -o ConnectTimeout=15 "$HOST" "
  docker run --rm -v /tmp:/t alpine rm -rf /t/cawp-chk-$TAG >/dev/null 2>&1; mkdir -p $DIR && tar -xzf - -C $DIR 2>/dev/null
  docker rm -f $DB $MY >/dev/null 2>&1; docker network rm $N >/dev/null 2>&1; docker network create $N >/dev/null
  docker run -d --rm --name $DB --network $N --tmpfs /var/lib/postgresql/data -e POSTGRES_PASSWORD=postgres postgres:16-alpine postgres -c fsync=off -c synchronous_commit=off >/dev/null
  docker run -d --rm --name $MY --network $N --network-alias cdr-test-db --tmpfs /var/lib/mysql -e MYSQL_ROOT_PASSWORD=cdr-test-root -e TZ=Asia/Riyadh -v $DIR/db/cdr-test/init:/docker-entrypoint-initdb.d:ro mysql:8.0 --skip-name-resolve --character-set-server=utf8mb4 >/dev/null
  for i in \$(seq 1 30); do docker exec $DB pg_isready -U postgres -h 127.0.0.1 >/dev/null 2>&1 && break; sleep 1; done; sleep 2
  for i in \$(seq 1 60); do docker exec $MY mysqladmin ping -h 127.0.0.1 -uroot -pcdr-test-root --silent >/dev/null 2>&1 && break; sleep 2; done
  docker run --rm --network $N -v $DIR:/repo -w /repo/CAWP-API -e TEST_DATABASE_URL=postgresql://postgres:postgres@$DB:5432/postgres -e PYTHONDONTWRITEBYTECODE=1 --entrypoint sh cawp-api -c \"pip install -q --disable-pip-version-check --root-user-action=ignore -r requirements-dev.txt >/dev/null 2>&1; python -m pytest -p no:cacheprovider -q -rs $ARGS > /repo/pytest.out 2>&1; echo PYTEST_EXIT=\\\$?; tail -40 /repo/pytest.out; echo SUMMARY:; grep -E '[0-9]+ (passed|failed|error)' /repo/pytest.out | tail -2\"
  docker rm -f $DB $MY >/dev/null 2>&1; docker network rm $N >/dev/null 2>&1; docker run --rm -v /tmp:/t alpine rm -rf /t/cawp-chk-$TAG"
