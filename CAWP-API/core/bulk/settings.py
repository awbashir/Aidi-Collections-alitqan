"""Environment settings of the reviewed bulk-job framework (read at call time, so tests and
the worker can change them without re-importing anything).

| Variable | Default | Meaning |
|---|---|---|
| `BULK_STORAGE_DIR` | `<UPLOAD_DIR>/private/bulk` | private root for uploads and artifacts (never served statically) |
| `BULK_UPLOAD_MAX_MB` | 20 | upload size limit (413 above it) |
| `BULK_XLSX_MAX_UNCOMPRESSED_MB` | 300 | zip-bomb guard: total uncompressed size of a workbook |
| `BULK_MAX_ROWS` | 100000 | most rows one job may hold (selection, filter, sheet) |
| `BULK_MAX_COLUMNS` | 200 | most columns read from a sheet |
| `BULK_UPLOAD_TTL_DAYS` | 7 | upload file lifetime before the retention task purges it |
| `BULK_ARTIFACT_TTL_DAYS` | 14 | preview / errors / results CSV lifetime |
| `BULK_PREVIEW_TTL_HOURS` | 24 | how long a `ready` preview may wait for confirmation |
| `BULK_BATCH_SIZE` | 500 | rows per execution batch (one transaction each) |
| `BULK_PREPARE_CHUNK` | 2000 | rows per validation chunk |
| `BULK_LEASE_SECONDS` | 60 | worker lease on a claimed job; renewed every third of it |
| `BULK_MAX_ATTEMPTS` | 3 | crashed/errored attempts before a job is failed |
| `BULK_WORKER_POLL_SECONDS` | 2 | idle poll interval of the worker |
| `BULK_RETENTION_INTERVAL_SECONDS` | 600 | how often the worker runs the retention task |
| `BULK_WORKER_HEALTH_FILE` | `/tmp/cawp-worker.alive` | liveness file touched by the worker loop |
| `BULK_WORKER_HEALTH_MAX_AGE` | 120 | healthcheck fails when the file is older (seconds) |
"""
from __future__ import annotations

import dataclasses
import os


def _int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _upload_dir() -> str:
    from core.config import Config
    return Config.UPLOAD_DIR


@dataclasses.dataclass(frozen=True)
class BulkSettings:
    storage_dir: str
    upload_max_bytes: int
    xlsx_max_uncompressed_bytes: int
    max_rows: int
    max_columns: int
    upload_ttl_days: int
    artifact_ttl_days: int
    preview_ttl_hours: int
    batch_size: int
    prepare_chunk: int
    lease_seconds: int
    max_attempts: int
    poll_seconds: int
    retention_interval_seconds: int
    health_file: str
    health_max_age: int


def get() -> BulkSettings:
    storage = os.getenv("BULK_STORAGE_DIR") or os.path.join(_upload_dir(), "private", "bulk")
    upload_max = os.getenv("BULK_UPLOAD_MAX_BYTES")        # tests: exact byte limit
    return BulkSettings(
        storage_dir=os.path.abspath(storage),
        upload_max_bytes=int(upload_max) if upload_max else _int("BULK_UPLOAD_MAX_MB", 20) * 1024 * 1024,
        xlsx_max_uncompressed_bytes=_int("BULK_XLSX_MAX_UNCOMPRESSED_MB", 300) * 1024 * 1024,
        max_rows=_int("BULK_MAX_ROWS", 100_000),
        max_columns=_int("BULK_MAX_COLUMNS", 200),
        upload_ttl_days=_int("BULK_UPLOAD_TTL_DAYS", 7),
        artifact_ttl_days=_int("BULK_ARTIFACT_TTL_DAYS", 14),
        preview_ttl_hours=_int("BULK_PREVIEW_TTL_HOURS", 24),
        batch_size=_int("BULK_BATCH_SIZE", 500),
        prepare_chunk=_int("BULK_PREPARE_CHUNK", 2000),
        lease_seconds=_int("BULK_LEASE_SECONDS", 60, minimum=5),
        max_attempts=_int("BULK_MAX_ATTEMPTS", 3),
        poll_seconds=_int("BULK_WORKER_POLL_SECONDS", 2),
        retention_interval_seconds=_int("BULK_RETENTION_INTERVAL_SECONDS", 600),
        health_file=os.getenv("BULK_WORKER_HEALTH_FILE") or "/tmp/cawp-worker.alive",
        health_max_age=_int("BULK_WORKER_HEALTH_MAX_AGE", 120),
    )
