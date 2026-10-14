#!/usr/bin/env bash
# Compare frontend tsc + eslint findings between HEAD and the working tree on the app server.
# Prints only NEW findings (line numbers stripped so moved lines don't count as new).
# Usage: tools/dev/fe_diff.sh <tag> <src files to eslint...>
set -u
TAG=${1:?tag}; shift
HOST=${CHECK_HOST:-aiserver@10.0.40.6}; DIR=/tmp/cawp-fed-$TAG
cd "$(dirname "$0")/../.."
S=$(mktemp -d); mkdir -p "$S/base" "$S/work"
git archive HEAD CAWP-frontend | tar -x -C "$S/base"
COPYFILE_DISABLE=1 tar -cf - --exclude=node_modules --exclude=build CAWP-frontend | tar -x -C "$S/work"
printf '%s\n' "$@" > "$S/files.txt"
COPYFILE_DISABLE=1 tar -czf - -C "$S" base work files.txt | ssh -o ConnectTimeout=15 "$HOST" "docker run --rm -v /tmp:/t alpine rm -rf /t/cawp-fed-$TAG >/dev/null 2>&1; mkdir -p $DIR && tar -xzf - -C $DIR 2>/dev/null
cat > $DIR/run.sh <<'IN'
set -u
cd /w/work/CAWP-frontend && npm ci --no-audit --no-fund >/dev/null 2>&1; cp -r node_modules /w/base/CAWP-frontend/ 2>/dev/null
for v in base work; do
  cd /w/\$v/CAWP-frontend
  npx tsc --noEmit -p tsconfig.json 2>&1 | grep 'error TS' | sed -E 's/\(([0-9]+),([0-9]+)\)//; s@/w/(base|work)/CAWP-frontend/@@g' | sort > /w/tsc.\$v
  ls \$(cat /w/files.txt) 2>/dev/null | xargs npx eslint -f unix 2>/dev/null | grep -E '\[Error' | sed -E 's/:[0-9]+:[0-9]+:/:/; s@/w/(base|work)/CAWP-frontend/@@g' | sort > /w/eslint.\$v
done
echo \"== tsc errors: base \$(wc -l < /w/tsc.base) work \$(wc -l < /w/tsc.work); NEW:\"; comm -13 /w/tsc.base /w/tsc.work | head -60
echo \"== eslint errors in given files: base \$(wc -l < /w/eslint.base) work \$(wc -l < /w/eslint.work); NEW:\"; comm -13 /w/eslint.base /w/eslint.work | head -60
IN
docker run --rm -v $DIR:/w node:22-alpine sh /w/run.sh; docker run --rm -v /tmp:/t alpine rm -rf /t/cawp-fed-$TAG"
rm -rf "$S"
