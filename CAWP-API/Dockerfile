# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

RUN mkdir -p /app/uploads

EXPOSE 9797

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
  CMD curl -fsS http://127.0.0.1:${PORT:-9797}/health || exit 1

# One gunicorn worker + many threads: Flask-SocketIO (threading mode) needs a single
# process unless a message queue is configured; the workload is I/O bound (Postgres).
CMD ["sh", "-c", "gunicorn --worker-class gthread --workers ${WEB_CONCURRENCY:-1} --threads ${GUNICORN_THREADS:-32} --timeout 300 --graceful-timeout 30 --keep-alive 5 --bind 0.0.0.0:${PORT:-9797} --access-logfile - --error-logfile - app:app"]
