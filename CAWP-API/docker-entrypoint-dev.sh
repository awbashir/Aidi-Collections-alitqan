#!/bin/sh
# Dev entrypoint: auto-reload on code change (used when RELOAD=1)
set -e
if [ "${RELOAD:-0}" = "1" ]; then
  exec gunicorn --worker-class gthread --workers 1 --threads "${GUNICORN_THREADS:-32}" --timeout 300 \
       --reload --bind "0.0.0.0:${PORT:-9797}" --access-logfile - --error-logfile - app:app
fi
exec gunicorn --worker-class gthread --workers "${WEB_CONCURRENCY:-1}" --threads "${GUNICORN_THREADS:-32}" --timeout 300 \
     --bind "0.0.0.0:${PORT:-9797}" --access-logfile - --error-logfile - app:app
